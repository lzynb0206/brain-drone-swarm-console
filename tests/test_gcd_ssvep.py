from __future__ import annotations

import unittest

import numpy as np

from src.ml.gcd_ssvep import (
    construct_connectivity_graphs,
    extract_ssvep_node_features,
    paper_bandpass,
    pretrain_gcd,
    predict_graph_classifier,
    train_graph_classifier,
    transform_graphs_with_gcd,
)


class GCDSSVEPGraphTest(unittest.TestCase):
    def setUp(self):
        fs = 250.0
        time = np.arange(500) / fs
        self.eeg = np.asarray(
            [
                25.0 * np.sin(2 * np.pi * 11.0 * time + index * 0.2)
                + 2.0 * np.sin(2 * np.pi * 7.0 * time)
                for index in range(8)
            ]
        )
        self.fs = fs

    def test_graph_construction_is_finite_symmetric_and_loop_free(self):
        filtered = paper_bandpass(self.eeg, self.fs)
        features = extract_ssvep_node_features(filtered, self.fs)
        graphs = construct_connectivity_graphs(filtered, self.fs)
        self.assertEqual(features.shape, (8, 10))
        self.assertTrue(np.isfinite(features).all())
        self.assertEqual(set(graphs), {"G_PCC", "G_MSC", "G_PLV"})
        for graph in graphs.values():
            self.assertEqual(graph.shape, (8, 8))
            self.assertTrue(np.isfinite(graph).all())
            np.testing.assert_allclose(graph, graph.T, atol=1e-7)
            np.testing.assert_allclose(np.diag(graph), 0.0, atol=1e-7)
            self.assertGreaterEqual(float(graph.min()), 0.0)
            self.assertLessEqual(float(graph.max()), 1.0)

    def test_gcn_and_gcd_gcn_train_and_predict_shapes(self):
        rng = np.random.default_rng(42)
        features = rng.normal(size=(14, 8, 10)).astype(np.float32)
        raw = rng.random(size=(14, 8, 8)).astype(np.float32)
        graphs = (raw + raw.transpose(0, 2, 1)) / 2.0
        for graph in graphs:
            np.fill_diagonal(graph, 0.0)
        labels = np.repeat(np.arange(7), 2)
        for use_gcd in (False, True):
            trained = train_graph_classifier(
                features,
                graphs,
                labels,
                num_classes=7,
                use_gcd=use_gcd,
                seed=42,
                pretraining_epochs=1,
                training_epochs=1,
            )
            predictions, probabilities = predict_graph_classifier(
                trained.model,
                features,
                graphs,
            )
            self.assertEqual(predictions.shape, (14,))
            self.assertEqual(probabilities.shape, (14, 7))
            np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, atol=1e-6)

    def test_gcd_preprocessor_keeps_invalid_nodes_disconnected(self):
        rng = np.random.default_rng(7)
        raw = rng.random(size=(14, 4, 4)).astype(np.float32)
        graphs = (raw + raw.transpose(0, 2, 1)) / 2.0
        for graph in graphs:
            np.fill_diagonal(graph, 0.0)
        labels = np.repeat(np.arange(7), 2)
        masks = np.ones((14, 4), dtype=bool)
        masks[:, -1] = False
        state, _ = pretrain_gcd(
            graphs,
            labels,
            num_nodes=4,
            seed=42,
            epochs=1,
            train_node_mask=masks,
        )
        transformed = transform_graphs_with_gcd(graphs, state, node_mask=masks)
        self.assertEqual(transformed.shape, graphs.shape)
        np.testing.assert_allclose(transformed[:, -1, :], 0.0)
        np.testing.assert_allclose(transformed[:, :, -1], 0.0)
        np.testing.assert_allclose(transformed, transformed.transpose(0, 2, 1))


if __name__ == "__main__":
    unittest.main()
