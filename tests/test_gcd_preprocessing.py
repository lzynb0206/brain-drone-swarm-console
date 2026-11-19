from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
import torch

from src.ml.gcd_preprocessing import (
    convert_labels_to_int,
    load_data,
    normalize_adjacency,
    preprocess_features,
)


class GCDPreprocessingTest(unittest.TestCase):
    def test_upstream_label_mapping(self):
        labels = convert_labels_to_int(np.array([b"preical", b"ical"]))
        np.testing.assert_array_equal(labels, np.array([0, 1]))

    def test_upstream_feature_fill_and_scaling(self):
        features = np.arange(2 * 3 * 4, dtype=np.float64).reshape(2, 3, 4)
        features[0, 0, 0] = np.nan
        features[1, 2, 3] = np.inf
        processed = preprocess_features(features)
        self.assertTrue(np.isfinite(processed).all())
        np.testing.assert_allclose(processed.reshape(-1, 4).mean(axis=0), 0.0, atol=1e-7)

    def test_upstream_adjacency_normalization(self):
        graphs = np.array([[[1.0, 1.0], [3.0, 1.0]]])
        normalized = normalize_adjacency(graphs)
        np.testing.assert_allclose(normalized, np.array([[[0.0, 1.0], [1.0, 0.0]]]))

    def test_h5_load_and_ten_fold_split(self):
        rng = np.random.default_rng(42)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "processed_data.h5"
            with h5py.File(path, "w") as file:
                # Upstream stores (samples, feature_dim, channels), then transposes it.
                file.create_dataset("node_features", data=rng.normal(size=(20, 4, 3)))
                file.create_dataset(
                    "labels",
                    data=np.array([b"preical", b"ical"] * 10),
                )
                file.create_dataset("G_PCC", data=rng.random(size=(20, 3, 3)))

            folds = load_data(path, graph_type="G_PCC", if_normalized_adj=True)

        self.assertEqual(len(folds), 10)
        self.assertEqual(tuple(folds[0]["train"]["features"].shape), (18, 3, 4))
        self.assertEqual(tuple(folds[0]["test"]["features"].shape), (2, 3, 4))
        self.assertEqual(folds[0]["train"]["features"].dtype, torch.float32)
        self.assertEqual(folds[0]["train"]["labels"].dtype, torch.long)
        diagonal = torch.diagonal(folds[0]["train"]["graphs"], dim1=-2, dim2=-1)
        self.assertTrue(torch.equal(diagonal, torch.zeros_like(diagonal)))


if __name__ == "__main__":
    unittest.main()
