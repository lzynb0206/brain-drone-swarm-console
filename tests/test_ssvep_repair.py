import unittest
import numpy as np
from src.ml.ssvep_repair import (quality_preprocess, complex_spectrum, crop_batch,
    reference_filterbank, fit_etrca, score_etrca, eegnet_streams)


class RepairTests(unittest.TestCase):
    def test_spectral_grid_and_zero_padding(self):
        for n in (300, 500, 1250):
            x = np.sin(2*np.pi*10*np.arange(n)/250)[None]
            z = complex_spectrum(x)
            self.assertEqual(z.shape, (1, 570))
            power = z[0,:285]**2+z[0,285:]**2
            self.assertEqual(int(power.argmax()), 15)
        with self.assertRaises(ValueError): complex_spectrum(np.zeros((1, 1251)))

    def test_bad_channels_zero_not_reordered(self):
        rng=np.random.default_rng(5)
        x=rng.normal(0, 5, (8,1250)); x[2]=0; x[5]=x[0]
        original=x.copy(); y,q=quality_preprocess(x)
        np.testing.assert_array_equal(original,x)
        self.assertEqual(y.shape,x.shape)
        self.assertTrue(np.all(y[2]==0)); self.assertTrue(np.all(y[5]==0))
        self.assertTrue(q['usable_mask'][0]); self.assertFalse(q['usable_mask'][5])
        a,b=eegnet_streams(y)
        self.assertTrue(np.all(a[2]==0)); self.assertTrue(np.all(b[:,2]==0))

    def test_crops_cannot_cross_trials_and_streams_align(self):
        a=np.arange(2*3*100).reshape(2,3,100)
        x,y=crop_batch((a,a*2),[1,0],[7,9],length=50,shifts=[13,4])
        np.testing.assert_array_equal(y,x*2)
        np.testing.assert_array_equal(x[0],np.roll(a[1,:,7:57],13,axis=-1))
        with self.assertRaises(ValueError): crop_batch((a,),[0],[70],length=50)

    def test_etrca_synthetic_and_independent_score(self):
        rng=np.random.default_rng(4); t=np.arange(500)/250
        y=np.repeat(np.arange(6),6)
        x=np.stack([np.sin(2*np.pi*(8+k)*t)[None]*np.array([1,.7,.3])[:,None]+rng.normal(0,.1,(3,500)) for k in y])
        b=reference_filterbank(x); before=b.copy()
        train=np.arange(len(y)).reshape(6,6)[:,:5].ravel(); test=np.arange(5,36,6)
        model=fit_etrca(b[train],y[train]); s=score_etrca(b[test],model)
        np.testing.assert_array_equal(s.argmax(1),y[test])
        independent=np.zeros_like(s)
        for i,k in enumerate(test):
            for j in range(6):
                for bank in range(3):
                    w=model[1][bank]
                    a=(b[k,bank].T@w).flatten(order='F')
                    c=(model[0][bank,j].T@w).flatten(order='F')
                    independent[i,j]+=((bank+1)**-1.25+.25)*np.corrcoef(a,c)[0,1]
        np.testing.assert_allclose(s,independent,atol=1e-10)
        np.testing.assert_array_equal(b,before)

if __name__=='__main__': unittest.main()
