from __future__ import annotations

import torch
from torch import Tensor, nn


class TemporalGCNBiLSTM(nn.Module):
    def __init__(
        self,
        feature_count: int,
        *,
        spatial_dimension: int = 32,
        temporal_hidden: int = 32,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.feature_count = feature_count
        self.spatial_dimension = spatial_dimension
        self.temporal_hidden = temporal_hidden
        self.dropout_probability = dropout
        self.gcn = nn.Linear(feature_count, spatial_dimension)
        self.temporal = nn.LSTM(
            input_size=spatial_dimension,
            hidden_size=temporal_hidden,
            batch_first=True,
            bidirectional=True,
        )
        self.head = nn.Sequential(
            nn.Linear(temporal_hidden * 2, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward_from_aggregated(self, aggregated: Tensor) -> Tensor:
        spatial = torch.relu(self.gcn(aggregated))
        batch, months, nodes, dimensions = spatial.shape
        sequences = spatial.permute(0, 2, 1, 3).reshape(
            batch * nodes, months, dimensions
        )
        temporal, _ = self.temporal(sequences)
        final_state = temporal[:, -1].reshape(batch, nodes, -1)
        return self.head(final_state).squeeze(-1)

    def forward(self, node_features: Tensor, adjacency: Tensor) -> Tensor:
        aggregated = torch.matmul(adjacency, node_features)
        return self.forward_from_aggregated(aggregated)
