"""SSVEP graph construction and GCD+GCN research model.

The public GCD repository starts from ``processed_data.h5`` and does not
contain the raw-EEG-to-graph conversion used by the paper.  This module fills
that gap for fixed-length, eight-channel SSVEP trials while retaining the
paper's core idea: contrastively pretrain an edge-weight mapping, then
fine-tune it with a downstream GNN.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Sequence

import numpy as np


DEFAULT_CHANNEL_NAMES = ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4")
DEFAULT_FREQUENCIES_HZ = (11.0, 13.75, 15.0, 16.5, 18.33, 20.625)
NODE_FEATURE_NAMES = (
    "log_power_11Hz",
    "log_power_13.75Hz",
    "log_power_15Hz",
    "log_power_16.5Hz",
    "log_power_18.33Hz",
    "log_power_20.625Hz",
    "log_total_power_0.5_40Hz",
    "log_standard_deviation",
    "spectral_entropy",
    "dominant_frequency_hz",
)


def _scipy_signal():
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("GCD SSVEP图构建需要 scipy") from exc
    return signal


def paper_bandpass(eeg, sample_rate_hz: float) -> np.ndarray:
    """Apply the paper's fifth-order 0.5-40 Hz zero-phase bandpass."""

    signal = _scipy_signal()
    data = np.asarray(eeg, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 100:
        raise ValueError("EEG must have shape (channels, samples)")
    if not np.isfinite(data).all():
        raise ValueError("EEG contains NaN or Inf")
    nyquist = float(sample_rate_hz) / 2.0
    if nyquist <= 40.0:
        raise ValueError("sample rate must be greater than 80 Hz")
    sos = signal.butter(
        5,
        [0.5 / nyquist, 40.0 / nyquist],
        btype="bandpass",
        output="sos",
    )
    return signal.sosfiltfilt(sos, data, axis=-1)


def extract_ssvep_node_features(
    filtered_eeg,
    sample_rate_hz: float,
    target_frequencies_hz: Sequence[float] = DEFAULT_FREQUENCIES_HZ,
) -> np.ndarray:
    """Return ten declared SSVEP features for every channel."""

    signal = _scipy_signal()
    data = np.asarray(filtered_eeg, dtype=np.float64)
    if len(target_frequencies_hz) != 6:
        raise ValueError("exactly six SSVEP target frequencies are required")
    frequencies, psd = signal.periodogram(
        data,
        fs=float(sample_rate_hz),
        axis=-1,
        detrend="linear",
        scaling="density",
    )
    eps = np.finfo(np.float64).tiny

    columns = []
    for target in target_frequencies_hz:
        mask = np.abs(frequencies - float(target)) <= 0.75
        if not np.any(mask):
            raise ValueError(f"no FFT bins around target frequency {target:g} Hz")
        columns.append(np.log10(np.sum(psd[:, mask], axis=1) + eps))

    analysis_band = (frequencies >= 0.5) & (frequencies <= 40.0)
    band_psd = psd[:, analysis_band]
    band_frequencies = frequencies[analysis_band]
    total_power = np.sum(band_psd, axis=1)
    columns.append(np.log10(total_power + eps))
    columns.append(np.log10(np.std(data, axis=1) + eps))

    probabilities = band_psd / np.maximum(total_power[:, None], eps)
    entropy = -np.sum(probabilities * np.log(probabilities + eps), axis=1)
    entropy /= np.log(max(2, band_psd.shape[1]))
    columns.append(entropy)
    columns.append(band_frequencies[np.argmax(band_psd, axis=1)])

    features = np.column_stack(columns)
    if features.shape != (data.shape[0], 10) or not np.isfinite(features).all():
        raise ValueError("failed to construct finite 10-dimensional node features")
    return features


def construct_connectivity_graphs(
    filtered_eeg,
    sample_rate_hz: float,
) -> dict[str, np.ndarray]:
    """Build absolute PCC, mean magnitude-squared coherence, and PLV graphs."""

    signal = _scipy_signal()
    data = np.asarray(filtered_eeg, dtype=np.float64)
    channel_count = data.shape[0]
    graphs = {
        "G_PCC": np.zeros((channel_count, channel_count), dtype=np.float64),
        "G_MSC": np.zeros((channel_count, channel_count), dtype=np.float64),
        "G_PLV": np.zeros((channel_count, channel_count), dtype=np.float64),
    }

    correlation = np.abs(np.corrcoef(data))
    graphs["G_PCC"][:] = np.nan_to_num(correlation, nan=0.0, posinf=0.0, neginf=0.0)

    phase = np.angle(signal.hilbert(data, axis=-1))
    nperseg = min(256, data.shape[1])
    for left in range(channel_count):
        for right in range(left + 1, channel_count):
            frequencies, coherence = signal.coherence(
                data[left],
                data[right],
                fs=float(sample_rate_hz),
                nperseg=nperseg,
                noverlap=nperseg // 2,
            )
            mask = (frequencies >= 0.5) & (frequencies <= 40.0)
            msc = float(np.mean(coherence[mask])) if np.any(mask) else 0.0
            plv = float(np.abs(np.mean(np.exp(1j * (phase[left] - phase[right])))))
            graphs["G_MSC"][left, right] = graphs["G_MSC"][right, left] = msc
            graphs["G_PLV"][left, right] = graphs["G_PLV"][right, left] = plv

    for graph in graphs.values():
        np.fill_diagonal(graph, 0.0)
        np.clip(graph, 0.0, 1.0, out=graph)
    return graphs


def trial_to_graph(
    eeg,
    sample_rate_hz: float,
    target_frequencies_hz: Sequence[float] = DEFAULT_FREQUENCIES_HZ,
) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray]:
    """Convert one raw trial to node features and three connectivity graphs."""

    filtered = paper_bandpass(eeg, sample_rate_hz)
    features = extract_ssvep_node_features(
        filtered,
        sample_rate_hz,
        target_frequencies_hz,
    )
    graphs = construct_connectivity_graphs(filtered, sample_rate_hz)
    return features, graphs, filtered


def _torch_modules():
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise RuntimeError("GCD训练需要 torch") from exc
    # The bundled PyTorch build on this Apple/Python 3.14 environment can
    # crash in its native thread pool on these very small dense matrices.
    # One CPU thread is faster for 8-node graphs and makes the run stable.
    torch.set_num_threads(1)
    return torch, nn


class GraphEmbeddingBase:
    """Typing marker; the runtime class is created by ``build_graph_embedding``."""


def build_graph_embedding(num_nodes: int, hidden_layers=(240, 240), dropout=0.5):
    torch, nn = _torch_modules()
    edge_count = int(num_nodes) * (int(num_nodes) - 1) // 2

    class GraphEmbedding(nn.Module):
        def __init__(self):
            super().__init__()
            layers = []
            current = edge_count
            for width in hidden_layers:
                layers.extend((nn.Linear(current, int(width)), nn.ReLU(), nn.Dropout(dropout)))
                current = int(width)
            layers.extend((nn.Linear(current, edge_count), nn.Sigmoid()))
            self.mlp = nn.Sequential(*layers)
            indices = torch.triu_indices(num_nodes, num_nodes, offset=1)
            self.register_buffer("edge_rows", indices[0])
            self.register_buffer("edge_cols", indices[1])

        def edge_embeddings(self, adjacency):
            edges = adjacency[:, self.edge_rows, self.edge_cols]
            return self.mlp(edges)

        def forward(self, adjacency):
            edges = self.edge_embeddings(adjacency)
            output = torch.zeros_like(adjacency)
            output[:, self.edge_rows, self.edge_cols] = edges
            output[:, self.edge_cols, self.edge_rows] = edges
            return output

    return GraphEmbedding()


def set_deterministic_seed(seed: int) -> None:
    torch, _ = _torch_modules()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def normalized_adjacency(adjacency):
    torch, _ = _torch_modules()
    node_count = adjacency.shape[-1]
    identity = torch.eye(node_count, dtype=adjacency.dtype, device=adjacency.device)
    values = adjacency + identity.unsqueeze(0)
    degree = values.sum(dim=-1).clamp_min(1e-8)
    inv_sqrt = torch.rsqrt(degree)
    return inv_sqrt.unsqueeze(-1) * values * inv_sqrt.unsqueeze(-2)


def repository_normalized_laplacian(adjacency):
    """Match ``models_GCN.py`` from the public GCD repository.

    The upstream implementation calls this a normalized Laplacian and applies
    ``I - D^-1/2 A D^-1/2`` independently inside every graph-convolution
    layer.  Keep it separate from :func:`normalized_adjacency`, which follows
    Eq. (22) in the paper rather than the released implementation.
    """

    torch, _ = _torch_modules()
    node_count = adjacency.shape[-1]
    degree = adjacency.sum(dim=-1).clamp_min(1e-8)
    inv_sqrt = torch.rsqrt(degree)
    normalized = inv_sqrt.unsqueeze(-1) * adjacency * inv_sqrt.unsqueeze(-2)
    identity = torch.eye(node_count, dtype=adjacency.dtype, device=adjacency.device)
    return identity.unsqueeze(0) - normalized


def _global_graph_similarity(edge_values, graph_embedding, num_nodes: int):
    torch, _ = _torch_modules()
    adjacency = torch.zeros(
        edge_values.shape[0],
        num_nodes,
        num_nodes,
        dtype=edge_values.dtype,
        device=edge_values.device,
    )
    adjacency[:, graph_embedding.edge_rows, graph_embedding.edge_cols] = edge_values
    adjacency[:, graph_embedding.edge_cols, graph_embedding.edge_rows] = edge_values
    degree = adjacency.sum(dim=-1).clamp_min(1e-8)
    inv_sqrt = torch.rsqrt(degree)
    normalized = inv_sqrt.unsqueeze(-1) * adjacency * inv_sqrt.unsqueeze(-2)
    identity = torch.eye(num_nodes, dtype=adjacency.dtype, device=adjacency.device)
    laplacian = identity.unsqueeze(0) - normalized
    eigenvalues = torch.linalg.eigvalsh(laplacian)[:, 1:]
    scale = eigenvalues.std(dim=0, unbiased=False).clamp_min(1e-4)
    difference = eigenvalues[:, None, :] - eigenvalues[None, :, :]
    # The released ``pretraining.py`` applies the Gaussian kernel per
    # eigenvalue and then averages the similarities.  Taking exp after the
    # mean, as the first SSVEP port did, is mathematically different and can
    # erase useful dimension-wise structure.
    return torch.exp(-difference.square() / (4.0 * scale.square())).mean(dim=-1)


def supervised_gcd_loss(
    edge_values,
    labels,
    graph_embedding,
    num_nodes: int,
    gamma: float = 0.001,
    temperature: float = 0.1,
    node_mask=None,
):
    """Match the labelled InfoNCE calculation in upstream ``pretraining.py``.

    This intentionally retains the repository's multi-positive masking
    convention.  It is not interchangeable with a standard SupCon loss; the
    public GCD results were produced with this formulation.
    """

    torch, _ = _torch_modules()
    if node_mask is not None:
        edge_mask = (
            node_mask[:, graph_embedding.edge_rows]
            * node_mask[:, graph_embedding.edge_cols]
        ).to(dtype=edge_values.dtype, device=edge_values.device)
        edge_values = edge_values * edge_mask
    normalized_edges = torch.nn.functional.normalize(edge_values, dim=1)
    local_similarity = normalized_edges @ normalized_edges.T
    global_similarity = _global_graph_similarity(
        edge_values,
        graph_embedding,
        num_nodes,
    )
    logits = (local_similarity + float(gamma) * global_similarity) / float(temperature)
    sample_count = labels.shape[0]
    nonself = ~torch.eye(sample_count, dtype=torch.bool, device=labels.device)
    positives = (labels[:, None] == labels[None, :]) & nonself
    positive_logits = logits * positives
    positive_sum = torch.exp(positive_logits).sum(dim=1)
    all_sum = torch.exp(logits).sum(dim=1)
    return -torch.log(positive_sum / all_sum.clamp_min(1e-12)).mean()


def pretrain_gcd(
    train_graphs,
    train_labels,
    *,
    num_nodes: int,
    seed: int,
    epochs: int = 30,
    learning_rate: float = 0.001,
    weight_decay: float = 0.0,
    gamma: float = 0.001,
    batch_size: int = 256,
    train_node_mask=None,
) -> tuple[dict[str, object], list[float]]:
    torch, _ = _torch_modules()
    set_deterministic_seed(seed)
    graph_embedding = build_graph_embedding(num_nodes)
    graph_embedding.train()
    graphs = torch.as_tensor(train_graphs, dtype=torch.float32)
    labels = torch.as_tensor(train_labels, dtype=torch.long)
    if train_node_mask is None:
        node_masks = torch.ones(
            (len(labels), num_nodes),
            dtype=torch.float32,
        )
    else:
        mask_values = np.asarray(train_node_mask, dtype=np.float32)
        if mask_values.shape != (len(labels), num_nodes):
            raise ValueError("train_node_mask must have shape (samples, nodes)")
        node_masks = torch.as_tensor(mask_values, dtype=torch.float32)
    optimizer = torch.optim.Adam(
        graph_embedding.parameters(),
        lr=float(learning_rate),
        weight_decay=float(weight_decay),
    )
    generator = torch.Generator().manual_seed(seed)
    effective_batch_size = min(int(batch_size), len(labels))
    losses = []
    for _ in range(int(epochs)):
        order = torch.randperm(len(labels), generator=generator)
        running = 0.0
        for start in range(0, len(labels), effective_batch_size):
            indices = order[start:start + effective_batch_size]
            batch_graphs = torch.index_select(graphs, 0, indices)
            batch_labels = torch.index_select(labels, 0, indices)
            batch_node_masks = torch.index_select(node_masks, 0, indices)
            optimizer.zero_grad()
            edge_values = graph_embedding.edge_embeddings(batch_graphs)
            loss = supervised_gcd_loss(
                edge_values,
                batch_labels,
                graph_embedding,
                num_nodes,
                gamma=gamma,
                node_mask=batch_node_masks,
            )
            loss.backward()
            optimizer.step()
            running += float(loss.detach()) * len(batch_labels)
        losses.append(running / len(labels))
    state = {key: value.detach().clone() for key, value in graph_embedding.state_dict().items()}
    return state, losses


def transform_graphs_with_gcd(
    graphs,
    gcd_state: dict[str, object],
    *,
    node_mask=None,
) -> np.ndarray:
    """Apply a pretrained GCD edge mapper without a downstream GNN.

    This is the bridge needed when GCD is used as a graph preprocessor before
    another decoder.  Invalid EEG nodes remain disconnected after the sigmoid
    edge mapper, matching the masking used by :func:`train_graph_classifier`.
    """

    torch, _ = _torch_modules()
    values = np.asarray(graphs, dtype=np.float32)
    if values.ndim != 3 or values.shape[1] != values.shape[2]:
        raise ValueError("graphs must have shape (samples, nodes, nodes)")
    sample_count, num_nodes, _ = values.shape
    if node_mask is None:
        mask_values = np.ones((sample_count, num_nodes), dtype=np.float32)
    else:
        mask_values = np.asarray(node_mask, dtype=np.float32)
        if mask_values.shape != (sample_count, num_nodes):
            raise ValueError("node_mask must have shape (samples, nodes)")
    graph_embedding = build_graph_embedding(num_nodes, dropout=0.0)
    graph_embedding.load_state_dict(copy.deepcopy(gcd_state))
    graph_embedding.eval()
    with torch.no_grad():
        output = graph_embedding(torch.as_tensor(values, dtype=torch.float32))
        mask_tensor = torch.as_tensor(mask_values, dtype=torch.float32)
        output = output * mask_tensor.unsqueeze(-1) * mask_tensor.unsqueeze(-2)
    return output.cpu().numpy()


def build_gcn_classifier(
    *,
    num_nodes: int,
    feature_dim: int,
    num_classes: int,
    gcd_state: dict[str, object] | None,
    dropout: float = 0.5,
):
    torch, nn = _torch_modules()

    class GCDGCN(nn.Module):
        def __init__(self):
            super().__init__()
            self.layer1_graph_embedding = None
            self.layer2_graph_embedding = None
            if gcd_state is not None:
                # ``models_GCN.py`` creates a separate graph embedding inside
                # each GraphConvolution layer and initializes both from the
                # same pretrained parameters.  Dropout is used during GCD
                # pretraining but not in these fine-tuning embeddings.
                self.layer1_graph_embedding = build_graph_embedding(
                    num_nodes,
                    dropout=0.0,
                )
                self.layer2_graph_embedding = build_graph_embedding(
                    num_nodes,
                    dropout=0.0,
                )
                self.layer1_graph_embedding.load_state_dict(copy.deepcopy(gcd_state))
                self.layer2_graph_embedding.load_state_dict(copy.deepcopy(gcd_state))
            self.layer1 = nn.Linear(feature_dim, 16, bias=False)
            self.layer2 = nn.Linear(16, 4, bias=False)
            self.dropout = nn.Dropout(dropout)
            self.classifier = nn.Linear(num_nodes * 4, num_classes)

        def forward(self, node_features, adjacency, node_mask=None):
            if node_mask is None:
                node_mask = torch.ones(
                    node_features.shape[:2],
                    dtype=node_features.dtype,
                    device=node_features.device,
                )
            else:
                node_mask = node_mask.to(
                    dtype=node_features.dtype,
                    device=node_features.device,
                )
            edge_mask = node_mask.unsqueeze(-1) * node_mask.unsqueeze(-2)
            node_features = node_features * node_mask.unsqueeze(-1)
            layer1_adjacency = adjacency
            layer2_adjacency = adjacency
            if self.layer1_graph_embedding is not None:
                layer1_adjacency = self.layer1_graph_embedding(adjacency)
                layer2_adjacency = self.layer2_graph_embedding(adjacency)
            # A missing/bad electrode must remain disconnected after the GCD
            # MLP.  Otherwise the sigmoid output can recreate edges incident
            # on a node that the quality screen explicitly removed.
            layer1_adjacency = layer1_adjacency * edge_mask
            layer2_adjacency = layer2_adjacency * edge_mask
            layer1_operator = repository_normalized_laplacian(layer1_adjacency)
            layer2_operator = repository_normalized_laplacian(layer2_adjacency)
            hidden = torch.relu(layer1_operator @ self.layer1(node_features))
            hidden = hidden * node_mask.unsqueeze(-1)
            hidden = self.dropout(hidden)
            hidden = layer2_operator @ self.layer2(hidden)
            hidden = hidden * node_mask.unsqueeze(-1)
            return self.classifier(hidden.reshape(hidden.shape[0], -1))

    return GCDGCN()


@dataclass(frozen=True)
class TrainedGraphClassifier:
    model: object
    losses: tuple[float, ...]
    pretraining_losses: tuple[float, ...]


def repository_label_smoothing_loss(outputs, targets, smoothing: float, classes: int):
    """KL label smoothing copied semantically from upstream ``train.py``."""

    torch, _ = _torch_modules()
    if classes < 2 or not 0.0 <= float(smoothing) < 1.0:
        raise ValueError("label smoothing requires classes>=2 and 0<=smoothing<1")
    confidence = 1.0 - float(smoothing)
    with torch.no_grad():
        distribution = torch.zeros_like(outputs)
        distribution.fill_(float(smoothing) / (int(classes) - 1))
        distribution.scatter_(1, targets.unsqueeze(1), confidence)
    # This is the same optimization objective as upstream KL divergence;
    # the omitted target-entropy term is constant with respect to the model.
    # Writing it explicitly also avoids a PyTorch 2.10/Python 3.14 native
    # crash observed for ``kl_div`` on this real EEG tensor layout.
    log_probabilities = outputs - torch.logsumexp(outputs, dim=1, keepdim=True)
    return -(distribution * log_probabilities).sum(dim=1).mean()


def train_graph_classifier(
    train_features,
    train_graphs,
    train_labels,
    *,
    num_classes: int,
    use_gcd: bool,
    seed: int,
    pretraining_epochs: int = 30,
    training_epochs: int = 50,
    train_node_mask=None,
) -> TrainedGraphClassifier:
    torch, _ = _torch_modules()
    features = np.asarray(train_features, dtype=np.float32)
    graphs = np.asarray(train_graphs, dtype=np.float32)
    labels = np.asarray(train_labels, dtype=np.int64)
    num_nodes, feature_dim = features.shape[1:]
    gcd_state = None
    pretraining_losses: list[float] = []
    if use_gcd:
        gcd_state, pretraining_losses = pretrain_gcd(
            graphs,
            labels,
            num_nodes=num_nodes,
            seed=seed,
            epochs=pretraining_epochs,
            train_node_mask=train_node_mask,
        )

    set_deterministic_seed(seed)
    model = build_gcn_classifier(
        num_nodes=num_nodes,
        feature_dim=feature_dim,
        num_classes=num_classes,
        gcd_state=gcd_state,
    )
    parameters = list(model.named_parameters())
    graph_parameters = [value for name, value in parameters if "graph_embedding" in name]
    base_parameters = [value for name, value in parameters if "graph_embedding" not in name]
    groups = [{"params": base_parameters, "lr": 0.001}]
    if graph_parameters:
        groups.append({"params": graph_parameters, "lr": 0.0005})
    optimizer = torch.optim.Adam(groups, weight_decay=0.001)
    feature_tensor = torch.as_tensor(features, dtype=torch.float32)
    graph_tensor = torch.as_tensor(graphs, dtype=torch.float32)
    label_tensor = torch.as_tensor(labels, dtype=torch.long)
    if train_node_mask is None:
        node_mask_tensor = torch.ones(features.shape[:2], dtype=torch.float32)
    else:
        node_mask = np.asarray(train_node_mask, dtype=np.float32)
        if node_mask.shape != features.shape[:2]:
            raise ValueError("train_node_mask must have shape (samples, nodes)")
        node_mask_tensor = torch.as_tensor(node_mask, dtype=torch.float32)
    generator = torch.Generator().manual_seed(seed)
    batch_size = min(256, len(labels))
    losses = []
    for _ in range(int(training_epochs)):
        model.train()
        running = 0.0
        if batch_size == len(labels):
            batches = ((feature_tensor, graph_tensor, label_tensor, node_mask_tensor),)
        else:
            order = torch.randperm(len(labels), generator=generator)
            batches = tuple(
                (
                    torch.index_select(feature_tensor, 0, order[start:start + batch_size]),
                    torch.index_select(graph_tensor, 0, order[start:start + batch_size]),
                    torch.index_select(label_tensor, 0, order[start:start + batch_size]),
                    torch.index_select(node_mask_tensor, 0, order[start:start + batch_size]),
                )
                for start in range(0, len(labels), batch_size)
            )
        for batch_features, batch_graphs, batch_labels, batch_node_mask in batches:
            optimizer.zero_grad()
            logits = model(batch_features, batch_graphs, batch_node_mask)
            loss = repository_label_smoothing_loss(
                logits,
                batch_labels,
                smoothing=0.1,
                classes=num_classes,
            )
            loss.backward()
            optimizer.step()
            running += float(loss.detach()) * len(batch_labels)
        losses.append(running / len(labels))
    return TrainedGraphClassifier(
        model=model,
        losses=tuple(losses),
        pretraining_losses=tuple(pretraining_losses),
    )


def predict_graph_classifier(
    model,
    features,
    graphs,
    node_mask=None,
) -> tuple[np.ndarray, np.ndarray]:
    torch, _ = _torch_modules()
    model.eval()
    feature_values = np.asarray(features, dtype=np.float32)
    if node_mask is None:
        mask_values = np.ones(feature_values.shape[:2], dtype=np.float32)
    else:
        mask_values = np.asarray(node_mask, dtype=np.float32)
        if mask_values.shape != feature_values.shape[:2]:
            raise ValueError("node_mask must have shape (samples, nodes)")
    with torch.no_grad():
        logits = model(
            torch.as_tensor(feature_values, dtype=torch.float32),
            torch.as_tensor(graphs, dtype=torch.float32),
            torch.as_tensor(mask_values, dtype=torch.float32),
        )
        probabilities = torch.softmax(logits, dim=1)
    return (
        probabilities.argmax(dim=1).cpu().numpy(),
        probabilities.cpu().numpy(),
    )
