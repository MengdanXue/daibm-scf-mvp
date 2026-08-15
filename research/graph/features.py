from __future__ import annotations

import numpy as np

from research.data.schema import STATE_FEATURE_NAMES, SyntheticDataset


GRAPH_FEATURE_NAMES = STATE_FEATURE_NAMES + (
    "inbound_amount",
    "inbound_count",
    "inbound_overdue_ratio",
    "inbound_average_delay",
    "outbound_amount",
    "outbound_count",
    "outbound_overdue_ratio",
    "outbound_average_delay",
    "in_degree",
    "out_degree",
)


def aggregate_node_features(dataset: SyntheticDataset) -> np.ndarray:
    months = dataset.config.months
    enterprise_count = dataset.config.enterprise_count
    output = np.zeros(
        (months, enterprise_count, len(GRAPH_FEATURE_NAMES)),
        dtype=np.float32,
    )
    output[:, :, : len(STATE_FEATURE_NAMES)] = dataset.states

    for month_index in range(months):
        month = month_index + 1
        active = (
            (dataset.relationship_active_months[:, 0] <= month)
            & (dataset.relationship_active_months[:, 1] >= month)
        )
        edges = dataset.relationships[active]
        observations = dataset.edge_observations[month_index, active]
        if edges.size == 0:
            continue
        suppliers = edges[:, 0]
        customers = edges[:, 1]
        amounts, counts, overdue, delays = observations.T

        inbound_degree = np.bincount(
            customers, minlength=enterprise_count
        ).astype(np.float32)
        outbound_degree = np.bincount(
            suppliers, minlength=enterprise_count
        ).astype(np.float32)
        inbound = np.zeros((enterprise_count, 4), dtype=np.float32)
        outbound = np.zeros((enterprise_count, 4), dtype=np.float32)
        np.add.at(inbound[:, 0], customers, amounts)
        np.add.at(inbound[:, 1], customers, counts)
        np.add.at(inbound[:, 2], customers, overdue)
        np.add.at(inbound[:, 3], customers, delays)
        np.add.at(outbound[:, 0], suppliers, amounts)
        np.add.at(outbound[:, 1], suppliers, counts)
        np.add.at(outbound[:, 2], suppliers, overdue)
        np.add.at(outbound[:, 3], suppliers, delays)

        inbound_nonzero = np.maximum(inbound_degree, 1.0)
        outbound_nonzero = np.maximum(outbound_degree, 1.0)
        inbound[:, 2:] /= inbound_nonzero[:, None]
        outbound[:, 2:] /= outbound_nonzero[:, None]
        output[month_index, :, 7:11] = inbound
        output[month_index, :, 11:15] = outbound
        output[month_index, :, 15] = inbound_degree
        output[month_index, :, 16] = outbound_degree
    return output
