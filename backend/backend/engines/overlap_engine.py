"""
OverlapEngine — computes pairwise portfolio overlap between Nippon India schemes.

Overlap is measured using Jaccard similarity on equity holdings:
    Jaccard(A, B) = |A ∩ B| / |A ∪ B|

where A and B are sets of stock names held by each scheme.

A weighted variant uses holding weights:
    WeightedOverlap(A, B) = Σ min(w_a, w_b) for stocks in A ∩ B

Outputs:
  - NxN overlap matrix (for heatmap rendering)
  - Edge list for network graph (nodes = schemes, edges = overlap score)
  - Per-scheme overlap summary (how much each scheme overlaps with others)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SchemeNode:
    """Node in the overlap network graph."""
    id: str
    name: str
    category: str | None
    instrument_type: str
    aum_cr: float | None
    holdings_count: int


@dataclass
class OverlapEdge:
    """Edge in the overlap network graph."""
    source_id: str
    target_id: str
    jaccard_score: float        # 0..1, unweighted
    weighted_overlap: float     # 0..1, weight-sensitive
    shared_stocks: int          # number of stocks in common
    shared_stock_names: list[str] = field(default_factory=list)


@dataclass
class HeatmapCell:
    row_scheme_id: str
    col_scheme_id: str
    jaccard_score: float
    weighted_overlap: float


@dataclass
class OverlapResult:
    """Full output of an overlap computation for a set of selected schemes."""
    scheme_ids: list[str]
    nodes: list[SchemeNode]
    edges: list[OverlapEdge]            # only edges with overlap > threshold
    heatmap: list[HeatmapCell]          # full NxN matrix (including diagonal)
    overlap_threshold: float            # edges only appear above this
    avg_pairwise_jaccard: float         # average across all pairs
    most_overlapping_pair: tuple[str, str] | None
    least_overlapping_pair: tuple[str, str] | None
    scheme_overlap_summary: list[dict]  # per-scheme avg overlap with selected peers


class OverlapEngine:
    """
    Loads scheme holdings from JSON and computes pairwise overlap matrices.

    Usage:
        engine = OverlapEngine(
            schemes_path="backend/data/schemes_metadata.json",
            holdings_path="backend/data/sample_holdings.json",
        )
        result = engine.compute_overlap(["nippon-large-cap-fund", "nippon-multi-cap-fund"])
    """

    def __init__(
        self,
        schemes_path: str | Path,
        holdings_path: str | Path,
        overlap_threshold: float = 0.05,
    ) -> None:
        self._schemes_path = Path(schemes_path)
        self._holdings_path = Path(holdings_path)
        self._overlap_threshold = overlap_threshold
        self._schemes: dict[str, dict] = {}
        self._holdings: dict[str, list[dict]] = {}
        self._load()

    def _load(self) -> None:
        with open(self._schemes_path, encoding="utf-8") as f:
            schemes_list = json.load(f)
        self._schemes = {s["id"]: s for s in schemes_list}

        with open(self._holdings_path, encoding="utf-8") as f:
            holdings_data = json.load(f)

        # holdings_path may come from sample_holdings.json (keyed by scheme_id)
        # or from the holdings embedded in schemes_metadata (under key "holdings")
        if isinstance(holdings_data, dict):
            for scheme_id, data in holdings_data.items():
                raw = data.get("holdings", []) if isinstance(data, dict) else data
                self._holdings[scheme_id] = [
                    h for h in raw
                    if not h.get("is_category_subtotal", False)
                    and h.get("name", "").lower()
                    not in ("equity less than 1% of corpus", "cash and other receivables", "grand total")
                ]
        else:
            # Fall through: holdings embedded in each scheme
            for s in schemes_list:
                if s.get("holdings"):
                    self._holdings[s["id"]] = s["holdings"]

        # Also pick up inline holdings from schemes_metadata for equity schemes
        for s in schemes_list:
            if s["id"] not in self._holdings and s.get("holdings"):
                self._holdings[s["id"]] = [
                    h for h in s["holdings"]
                    if h.get("name", "").lower()
                    not in ("equity less than 1% of corpus", "cash and other receivables")
                ]

    def _get_stock_set(self, scheme_id: str) -> set[str]:
        """Return the set of stock names held by a scheme (normalised)."""
        holdings = self._holdings.get(scheme_id, [])
        return {h["name"].strip().rstrip("*").lower() for h in holdings if h.get("weight", 0) > 0}

    def _get_weight_map(self, scheme_id: str) -> dict[str, float]:
        """Return {stock_name: weight} map for a scheme."""
        holdings = self._holdings.get(scheme_id, [])
        return {
            h["name"].strip().rstrip("*").lower(): h.get("weight", 0.0)
            for h in holdings
            if h.get("weight", 0) > 0
        }

    def _jaccard(self, set_a: set[str], set_b: set[str]) -> float:
        """Jaccard similarity: |A ∩ B| / |A ∪ B|"""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _weighted_overlap(
        self, weights_a: dict[str, float], weights_b: dict[str, float]
    ) -> float:
        """
        Weight-aware overlap: sum of min(w_a, w_b) for shared stocks.
        Result is in percentage points (0..100), normalized by dividing by 100.
        """
        shared = set(weights_a.keys()) & set(weights_b.keys())
        raw = sum(min(weights_a[s], weights_b[s]) for s in shared)
        return raw / 100.0  # convert from % to 0-1 scale

    def compute_overlap(
        self,
        scheme_ids: list[str],
        overlap_threshold: float | None = None,
        max_shared_stocks_in_response: int = 10,
    ) -> OverlapResult:
        """
        Compute pairwise overlap for a list of scheme IDs.

        Only equity schemes can have holdings-based overlap computed.
        Non-equity schemes (index, ETF, debt) appear as nodes but will show
        zero overlap (holdings not available in the dataset).

        Args:
            scheme_ids: list of scheme IDs to analyse (from user's selection)
            overlap_threshold: edges below this Jaccard score are omitted from
                               the network graph (default: self._overlap_threshold)
            max_shared_stocks_in_response: cap shared stock name lists for response size

        Returns:
            OverlapResult with heatmap, network nodes/edges, and summary stats.
        """
        threshold = overlap_threshold if overlap_threshold is not None else self._overlap_threshold
        n = len(scheme_ids)

        # Build nodes
        nodes: list[SchemeNode] = []
        for sid in scheme_ids:
            s = self._schemes.get(sid, {})
            nodes.append(SchemeNode(
                id=sid,
                name=s.get("name", sid),
                category=s.get("category"),
                instrument_type=s.get("instrument_type", "unknown"),
                aum_cr=s.get("aum_cr"),
                holdings_count=len(self._holdings.get(sid, [])),
            ))

        # Precompute sets and weight maps
        stock_sets: dict[str, set[str]] = {sid: self._get_stock_set(sid) for sid in scheme_ids}
        weight_maps: dict[str, dict[str, float]] = {
            sid: self._get_weight_map(sid) for sid in scheme_ids
        }

        # Compute pairwise overlap
        heatmap: list[HeatmapCell] = []
        edges: list[OverlapEdge] = []
        pairwise_scores: list[float] = []
        per_scheme_scores: dict[str, list[float]] = {sid: [] for sid in scheme_ids}

        for i in range(n):
            for j in range(n):
                sid_a = scheme_ids[i]
                sid_b = scheme_ids[j]
                jaccard = self._jaccard(stock_sets[sid_a], stock_sets[sid_b])
                w_overlap = self._weighted_overlap(weight_maps[sid_a], weight_maps[sid_b])

                heatmap.append(HeatmapCell(
                    row_scheme_id=sid_a,
                    col_scheme_id=sid_b,
                    jaccard_score=round(jaccard, 4),
                    weighted_overlap=round(w_overlap, 4),
                ))

                if i < j:  # upper triangle only for edges/stats
                    pairwise_scores.append(jaccard)
                    per_scheme_scores[sid_a].append(jaccard)
                    per_scheme_scores[sid_b].append(jaccard)

                    if jaccard >= threshold:
                        shared_names = sorted(
                            stock_sets[sid_a] & stock_sets[sid_b]
                        )[:max_shared_stocks_in_response]
                        edges.append(OverlapEdge(
                            source_id=sid_a,
                            target_id=sid_b,
                            jaccard_score=round(jaccard, 4),
                            weighted_overlap=round(w_overlap, 4),
                            shared_stocks=len(stock_sets[sid_a] & stock_sets[sid_b]),
                            shared_stock_names=shared_names,
                        ))

        # Sort edges by overlap (highest first)
        edges.sort(key=lambda e: e.jaccard_score, reverse=True)

        # Summary stats
        avg_jaccard = sum(pairwise_scores) / len(pairwise_scores) if pairwise_scores else 0.0
        most_overlap = (
            (edges[0].source_id, edges[0].target_id) if edges else None
        )
        # Least overlapping pair from all pairs (including those below threshold)
        all_pairs = [
            (scheme_ids[i], scheme_ids[j],
             self._jaccard(stock_sets[scheme_ids[i]], stock_sets[scheme_ids[j]]))
            for i in range(n) for j in range(i + 1, n)
        ]
        least_pair = (
            min(all_pairs, key=lambda x: x[2])[:2]
            if all_pairs else None
        )

        scheme_overlap_summary = [
            {
                "scheme_id": sid,
                "scheme_name": self._schemes.get(sid, {}).get("name", sid),
                "avg_overlap_with_peers": round(
                    sum(per_scheme_scores[sid]) / len(per_scheme_scores[sid]), 4
                ) if per_scheme_scores[sid] else 0.0,
                "has_holdings_data": len(self._holdings.get(sid, [])) > 0,
            }
            for sid in scheme_ids
        ]

        return OverlapResult(
            scheme_ids=scheme_ids,
            nodes=nodes,
            edges=edges,
            heatmap=heatmap,
            overlap_threshold=threshold,
            avg_pairwise_jaccard=round(avg_jaccard, 4),
            most_overlapping_pair=most_overlap,
            least_overlapping_pair=least_pair,
            scheme_overlap_summary=scheme_overlap_summary,
        )
