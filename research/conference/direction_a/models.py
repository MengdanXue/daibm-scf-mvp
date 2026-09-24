from __future__ import annotations

import torch
from torch import Tensor, nn


def combine_bidirectional_hidden(hidden: Tensor) -> Tensor:
    """Concatenate the true final forward/backward hidden states of a 1-layer BiLSTM."""
    if hidden.ndim != 3 or hidden.shape[0] != 2:
        raise ValueError("expected one-layer bidirectional hidden state with shape [2,batch,hidden]")
    return torch.cat((hidden[0], hidden[1]), dim=-1)


class ConferenceLSTM(nn.Module):
    def __init__(self, feature_count: int, hidden: int = 24, dropout: float = 0.1) -> None:
        super().__init__()
        self.temporal = nn.LSTM(feature_count, hidden, batch_first=True, bidirectional=True)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden * 2, 1))

    def forward(self, node_features: Tensor) -> Tensor:
        batch, months, nodes, dimensions = node_features.shape
        sequences = node_features.permute(0, 2, 1, 3).reshape(batch * nodes, months, dimensions)
        _, (hidden, _) = self.temporal(sequences)
        representation = combine_bidirectional_hidden(hidden).reshape(batch, nodes, -1)
        return self.head(representation).squeeze(-1)


class ConferenceGCN(nn.Module):
    def __init__(self, feature_count: int, hidden: int = 24, dropout: float = 0.1) -> None:
        super().__init__()
        self.encoder = nn.Linear(feature_count, hidden)
        self.head = nn.Sequential(nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, node_features: Tensor, adjacency: Tensor) -> Tensor:
        aggregated = torch.matmul(adjacency, node_features)
        temporal_mean = aggregated.mean(dim=1)
        return self.head(self.encoder(temporal_mean)).squeeze(-1)


class ConferenceTemporalGCNBiLSTM(nn.Module):
    """Minimal GCN + BiLSTM model for Direction A conference reruns.

    This class deliberately does not replace the defence ``TemporalGCNBiLSTM``
    or its published v0.4 artifact.
    """

    def __init__(
        self,
        feature_count: int,
        spatial_dimension: int = 24,
        temporal_hidden: int = 24,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.gcn = nn.Linear(feature_count, spatial_dimension)
        self.temporal = nn.LSTM(
            spatial_dimension,
            temporal_hidden,
            batch_first=True,
            bidirectional=True,
        )
        self.head = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(temporal_hidden * 2, 1),
        )

    def forward(self, node_features: Tensor, adjacency: Tensor) -> Tensor:
        aggregated = torch.matmul(adjacency, node_features)
        spatial = torch.relu(self.gcn(aggregated))
        batch, months, nodes, dimensions = spatial.shape
        sequences = spatial.permute(0, 2, 1, 3).reshape(batch * nodes, months, dimensions)
        _, (hidden, _) = self.temporal(sequences)
        representation = combine_bidirectional_hidden(hidden).reshape(batch, nodes, -1)
        return self.head(representation).squeeze(-1)
