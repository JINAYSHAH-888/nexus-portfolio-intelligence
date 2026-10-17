"use client";

import React, { useState, useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getOverlap, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, StatCard, DemoBadge } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion } from "motion/react";
import { AlertTriangle, Layers, Grid } from "lucide-react";
import { schemeShortName } from "@/lib/utils";

export default function OverlapAnalysisPage() {
  const [viewMode, setViewMode] = useState<"Network" | "Heatmap">("Network");

  const { data: selected, isLoading: selLoading } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  const schemeCount = selected?.selected_scheme_ids?.length ?? 0;

  const { data: overlap, isLoading, isError, error } = useQuery({
    queryKey: ["overlap"],
    queryFn: getOverlap,
    enabled: schemeCount >= 2,
  });

  const nodeCoordinates = useMemo(() => {
    const coords: Record<string, { x: number; y: number }> = {};
    const nodes = overlap?.nodes ?? [];
    const count = nodes.length;
    const centerX = 200;
    const centerY = 200;
    const radius = 120;

    nodes.forEach((n, idx) => {
      const angle = (2 * Math.PI * idx) / count - Math.PI / 2;
      coords[n.id] = {
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle),
      };
    });
    return coords;
  }, [overlap]);

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 2) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="Overlap Engine"
          title="Holdings Redundancy Audit"
          description="Calculates real Jaccard similarity across factsheet-disclosed stock holdings."
        />
        <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10 max-w-xl mx-auto space-y-4">
          <AlertTriangle className="w-8 h-8 text-crimson mx-auto" />
          <p className="font-mono text-xs text-crimson uppercase tracking-wider font-bold">MULTIPLE POSITIONS REQUIRED</p>
          <p className="text-sm text-ink-soft leading-relaxed">
            The overlap engine requires at least two selected schemes to compare. Currently, you have {schemeCount}.
          </p>
          <Link href="/schemes" className="inline-block border border-ink px-4 py-2 text-xs font-mono font-bold uppercase hover:bg-ink hover:text-paper transition-colors cursor-pointer">
            Select Schemes
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) return <LoadingState label="Loading overlap analysis…" />;
  if (isError) return <QueryError error={error} />;
  if (!overlap) return null;

  const nodesById = new Map(overlap.nodes.map((n) => [n.id, n]));
  const mostPairNames = overlap.most_overlapping_pair
    ? overlap.most_overlapping_pair.map((id) => nodesById.get(id)?.name ?? id)
    : null;
  const leastPairNames = overlap.least_overlapping_pair
    ? overlap.least_overlapping_pair.map((id) => nodesById.get(id)?.name ?? id)
    : null;
  const mostEdge = overlap.most_overlapping_pair
    ? overlap.edges.find(
        (e) =>
          (e.source_id === overlap.most_overlapping_pair![0] && e.target_id === overlap.most_overlapping_pair![1]) ||
          (e.source_id === overlap.most_overlapping_pair![1] && e.target_id === overlap.most_overlapping_pair![0])
      )
    : null;
  const leastEdge = overlap.least_overlapping_pair
    ? overlap.edges.find(
        (e) =>
          (e.source_id === overlap.least_overlapping_pair![0] && e.target_id === overlap.least_overlapping_pair![1]) ||
          (e.source_id === overlap.least_overlapping_pair![1] && e.target_id === overlap.least_overlapping_pair![0])
      )
    : null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="space-y-8"
    >
      <PageHeader
        eyebrow="Overlap Engine"
        title="Where your schemes overlap"
        description="Jaccard similarity across factsheet-disclosed holdings — real overlap, not an estimate."
        action={<DemoBadge />}
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 select-none">
        <StatCard
          variant="ink"
          label="Avg Pairwise Overlap"
          value={
            <div className="flex items-baseline gap-1">
              <span>{(overlap.avg_pairwise_jaccard * 100).toFixed(1)}%</span>
              <span className="text-xs font-mono text-text-muted uppercase">Jaccard similarity</span>
            </div>
          }
        />
        <StatCard
          label="Highest Overlap Pair"
          value={
            <div className="space-y-1">
              <div className="font-display text-sm truncate font-semibold">
                {mostPairNames ? mostPairNames.map(schemeShortName).join(" ↔ ") : "—"}
              </div>
              {mostEdge && (
                <div className="text-xs font-mono font-bold text-crimson">
                  Overlap of {(mostEdge.jaccard_score * 100).toFixed(1)}%
                </div>
              )}
            </div>
          }
        />
        <StatCard
          label="Least Overlap Pair"
          value={
            <div className="space-y-1">
              <div className="font-display text-sm truncate font-semibold">
                {leastPairNames ? leastPairNames.map(schemeShortName).join(" ↔ ") : "—"}
              </div>
              {leastEdge && (
                <div className="text-xs font-mono font-bold text-success">
                  Overlap of {(leastEdge.jaccard_score * 100).toFixed(1)}%
                </div>
              )}
            </div>
          }
        />
      </div>

      <div className="border border-line p-4 bg-paper flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 select-none">
        <span className="text-xs font-mono font-bold uppercase tracking-wider text-ink-soft">Graphical Perspective Mode:</span>
        <div className="flex gap-1 border border-line p-0.5 bg-paper-tint/10">
          <button
            onClick={() => setViewMode("Network")}
            className={`px-4 py-2 text-xs font-mono uppercase tracking-wide transition-colors flex items-center gap-2 cursor-pointer ${
              viewMode === "Network" ? "bg-ink text-paper font-semibold" : "text-ink-soft hover:text-ink"
            }`}
          >
            <Layers className="w-3.5 h-3.5" /> Network Graph
          </button>
          <button
            onClick={() => setViewMode("Heatmap")}
            className={`px-4 py-2 text-xs font-mono uppercase tracking-wide transition-colors flex items-center gap-2 cursor-pointer ${
              viewMode === "Heatmap" ? "bg-ink text-paper font-semibold" : "text-ink-soft hover:text-ink"
            }`}
          >
            <Grid className="w-3.5 h-3.5" /> Overlap Heatmap
          </button>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 border border-line p-6 md:p-8 bg-paper flex justify-center items-center min-h-[480px]">
          {viewMode === "Network" ? (
            <div className="w-full max-w-[440px] aspect-square relative select-none">
              <svg viewBox="0 0 400 400" className="w-full h-full overflow-visible">
                {overlap.edges
                  .filter((e) => e.jaccard_score >= overlap.overlap_threshold)
                  .map((pair, idx) => {
                    const posA = nodeCoordinates[pair.source_id];
                    const posB = nodeCoordinates[pair.target_id];
                    if (!posA || !posB) return null;

                    const pct = pair.jaccard_score * 100;
                    const strokeWidth = Math.max(1, Math.min(6, pct / 15));
                    const opacity = Math.min(0.85, Math.max(0.15, pct / 100));

                    let x1 = posA.x, y1 = posA.y, x2 = posB.x, y2 = posB.y;
                    if (x1 > x2 || (x1 === x2 && y1 > y2)) {
                      [x1, y1, x2, y2] = [x2, y2, x1, y1];
                    }
                    const midX = (x1 + x2) / 2;
                    const midY = (y1 + y2) / 2;
                    const dx = x2 - x1, dy = y2 - y1;
                    const len = Math.sqrt(dx * dx + dy * dy);
                    let labelX = midX, labelY = midY;
                    if (len > 0) {
                      const nx = -dy / len, ny = dx / len, offset = 14;
                      labelX = midX + nx * offset;
                      labelY = midY + ny * offset;
                    }
                    const textVal = `${pct.toFixed(0)}%`;
                    const badgeWidth = textVal.length * 5.5 + 10;
                    const badgeHeight = 15;

                    return (
                      <g key={idx}>
                        <motion.line
                          x1={posA.x} y1={posA.y} x2={posB.x} y2={posB.y}
                          stroke="var(--color-crimson)"
                          strokeWidth={strokeWidth}
                          strokeOpacity={opacity}
                          initial={{ pathLength: 0 }}
                          animate={{ pathLength: 1 }}
                          transition={{ duration: 1 }}
                        />
                        <g transform={`translate(${labelX}, ${labelY})`}>
                          <rect
                            x={-badgeWidth / 2} y={-badgeHeight / 2}
                            width={badgeWidth} height={badgeHeight} rx={3}
                            fill="var(--color-paper)" stroke="var(--color-line)" strokeWidth="1"
                          />
                          <text textAnchor="middle" y={3} className="font-mono text-[9px] font-bold fill-crimson">
                            {textVal}
                          </text>
                        </g>
                      </g>
                    );
                  })}

                {overlap.nodes.map((n) => {
                  const pos = nodeCoordinates[n.id];
                  if (!pos) return null;
                  return (
                    <g key={n.id} className="cursor-pointer">
                      <motion.circle
                        cx={pos.x} cy={pos.y} r={24}
                        fill="var(--color-paper)" stroke="var(--color-ink)" strokeWidth="1.5"
                        whileHover={{ scale: 1.12, stroke: "var(--color-crimson)" }}
                      />
                      <text
                        x={pos.x} y={pos.y + 3} textAnchor="middle"
                        className="font-mono text-[9px] font-bold fill-ink uppercase pointer-events-none"
                      >
                        {schemeShortName(n.name).slice(0, 3).toUpperCase()}
                      </text>
                      <title>{n.name}</title>
                    </g>
                  );
                })}
              </svg>
            </div>
          ) : (
            <div className="w-full overflow-x-auto select-none py-4">
              <div className="min-w-[420px] max-w-lg mx-auto">
                <div className="grid" style={{ gridTemplateColumns: `100px repeat(${overlap.nodes.length}, minmax(0, 1fr))` }}>
                  <div className="h-10" />
                  {overlap.nodes.map((n) => (
                    <div key={n.id} className="h-10 flex items-center justify-center font-mono text-[9px] font-bold text-ink-soft uppercase text-center truncate px-1">
                      {schemeShortName(n.name).slice(0, 4).toUpperCase()}
                    </div>
                  ))}
                </div>

                {overlap.nodes.map((rowNode) => (
                  <div key={rowNode.id} className="grid" style={{ gridTemplateColumns: `100px repeat(${overlap.nodes.length}, minmax(0, 1fr))` }}>
                    <div className="h-12 flex items-center font-mono text-[9px] font-bold text-ink-soft uppercase truncate pr-2 justify-end">
                      {schemeShortName(rowNode.name).slice(0, 4).toUpperCase()}
                    </div>
                    {overlap.nodes.map((colNode) => {
                      const cell = overlap.heatmap.find(
                        (c) => c.row_scheme_id === rowNode.id && c.col_scheme_id === colNode.id
                      );
                      const pct = cell ? cell.jaccard_score * 100 : rowNode.id === colNode.id ? 100 : 0;
                      const isDiagonal = rowNode.id === colNode.id;
                      const opacity = isDiagonal ? 0.05 : pct / 100;
                      const bgStyle = isDiagonal
                        ? { backgroundColor: "var(--color-ink-soft)", color: "var(--color-ink)" }
                        : { backgroundColor: `color-mix(in oklch, var(--accent) ${Math.round(opacity * 100)}%, transparent)`, color: pct > 35 ? "var(--text)" : "var(--color-ink)" };

                      return (
                        <div
                          key={colNode.id}
                          style={bgStyle}
                          className="h-12 relative flex cursor-help items-center justify-center border border-line font-mono text-xs font-bold tabular-nums outline-offset-[-2px] transition-[outline-color] duration-150 outline-2 outline-transparent hover:z-10 hover:outline-accent focus-visible:z-10 focus-visible:outline-accent"
                          title={`${rowNode.name} vs ${colNode.name}: ${pct.toFixed(1)}% Overlap`}
                        >
                          {pct.toFixed(0)}%
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <h3 className="font-display text-lg font-semibold text-ink select-none">Shared Holdings by Pair</h3>
          <div className="space-y-3 overflow-y-auto max-h-[480px] pr-1">
            {overlap.edges
              .slice()
              .sort((a, b) => b.jaccard_score - a.jaccard_score)
              .map((pair, idx) => {
                const nA = nodesById.get(pair.source_id);
                const nB = nodesById.get(pair.target_id);
                return (
                  <div key={idx} className="border border-line p-4 bg-paper hover:border-ink transition-colors select-none">
                    <div className="flex justify-between items-start mb-2">
                      <div className="text-xs font-sans font-bold text-ink leading-tight">
                        {nA && schemeShortName(nA.name)} <span className="text-ink-soft">↔</span> {nB && schemeShortName(nB.name)}
                      </div>
                      <span className="font-mono text-xs font-bold text-crimson bg-crimson/5 border border-crimson/20 px-2 py-0.5">
                        {(pair.jaccard_score * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div className="mt-3">
                      <span className="block text-[9px] font-mono uppercase tracking-wide text-ink-soft/70 mb-1.5 font-semibold">Shared underlying holdings</span>
                      {pair.shared_stock_names.length === 0 ? (
                        <span className="text-[10px] text-success font-semibold font-sans">✓ No mutual shares detected across core factsheet weights</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {pair.shared_stock_names.slice(0, 5).map(stock => (
                            <span key={stock} className="text-[9px] font-mono border border-line bg-paper-tint/30 px-1.5 py-0.5 text-ink-soft">
                              {stock}
                            </span>
                          ))}
                          {pair.shared_stock_names.length > 5 && (
                            <span className="text-[9px] font-mono text-ink-soft/50 pl-1 font-semibold">+{pair.shared_stock_names.length - 5} more</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>
        </div>

      </div>

    </motion.div>
  );
}
