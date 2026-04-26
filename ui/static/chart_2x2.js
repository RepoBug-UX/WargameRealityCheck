// 2x2: structural sensitivity (Y) by absolute disagreement (X).
// Inline SVG, vanilla JS. The chart is a figure, not the page.
// Color and typography are inherited from CSS — this script renders
// geometry only. Click any point to navigate to the drilldown.

function _clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function _emptyMessage(container) {
  _clear(container);
  const p = document.createElement("p");
  p.style.color = "var(--text-muted)";
  p.style.fontStyle = "italic";
  p.style.textAlign = "center";
  p.textContent =
    "No strict-tier disagreements yet — the 2x2 has nothing to plot.";
  container.appendChild(p);
}

function render2x2(container, audit, namespace) {
  if (!audit || !audit.strict_panel || !audit.strict_panel.length) {
    _emptyMessage(container);
    return;
  }

  // Build a sensitivity lookup from audit.branches.
  const sensByBranch = {};
  for (const b of audit.branches) sensByBranch[b.branch_id] = b.sensitivity;

  // Action set for highlighting.
  const actionSet = new Set(audit.action_list || []);

  // Layout
  const W = 640, H = 380;
  const M = { top: 28, right: 28, bottom: 56, left: 56 };
  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;

  // Domains: |Δ| in [0, max(|Δ|, 0.2)], sensitivity in [0, 1]
  const xMax = Math.max(0.2, ...audit.strict_panel.map(d => d.abs_delta));
  const yMax = 1.0;

  // Detect the degenerate case where every branch has the same
  // sensitivity (typical for wargames whose branches are independent
  // forward-looking questions, e.g. the illustrative fixture). When this
  // happens the 2x2 collapses to a 1D ranking by disagreement: we
  // center the points vertically and suppress the Y-axis decoration so
  // the chart honestly shows what the data is.
  const sensVals = audit.strict_panel.map(d => sensByBranch[d.branch_id] || 0);
  const sensRange = Math.max(...sensVals) - Math.min(...sensVals);
  const sensDegenerate = sensRange < 0.001;

  // Reserve a small left-pad so x=0 points are not cut off. Y-axis pad
  // only matters when the chart is non-degenerate.
  const X_PAD = 10;
  const Y_PAD = 12;
  const xScale = v => M.left + X_PAD + (v / xMax) * (innerW - X_PAD);
  const yScale = v => sensDegenerate
    ? M.top + innerH / 2  // center all points vertically when degenerate
    : M.top + innerH - Y_PAD - (v / yMax) * (innerH - Y_PAD);

  // Quadrant cutoffs — mirror ranking.py: 50th percentile or include all if n<=2.
  const xs = audit.strict_panel.map(d => d.abs_delta).sort((a, b) => a - b);
  const ys = audit.strict_panel
    .map(d => sensByBranch[d.branch_id] || 0).sort((a, b) => a - b);
  const pct = (arr, p) => {
    if (!arr.length) return 0;
    if (arr.length === 1) return arr[0];
    const k = (arr.length - 1) * (p / 100);
    const lo = Math.floor(k), hi = Math.min(lo + 1, arr.length - 1);
    return arr[lo] * (1 - (k - lo)) + arr[hi] * (k - lo);
  };
  const xCut = audit.strict_panel.length <= 2 ? 0 : pct(xs, 50);
  const yCut = audit.strict_panel.length <= 2 ? 0 : pct(ys, 50);

  // SVG construction helper
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  };

  const svg = make("svg", {
    class: "chart-2x2",
    viewBox: `0 0 ${W} ${H}`,
    width: "100%",
    style: "max-width: 640px;",
  });

  // Action-quadrant tint (top-right). Suppressed when sensitivity is
  // degenerate — there is no "top half" and the tint would just shade
  // most of the plot for no informational gain.
  if (!sensDegenerate) {
    svg.appendChild(make("rect", {
      class: "quadrant-action",
      x: xScale(xCut),
      y: yScale(yMax),
      width: xScale(xMax) - xScale(xCut),
      height: yScale(yCut) - yScale(yMax),
    }));
  }

  // Axes
  svg.appendChild(make("line", {
    class: "axis-line",
    x1: M.left, y1: H - M.bottom,
    x2: W - M.right, y2: H - M.bottom,
  }));
  svg.appendChild(make("line", {
    class: "axis-line",
    x1: M.left, y1: M.top,
    x2: M.left, y2: H - M.bottom,
  }));

  // Quadrant-cut gridlines. The X gridline is always meaningful;
  // the Y gridline is meaningless when sensitivity is degenerate.
  svg.appendChild(make("line", {
    class: "gridline",
    x1: xScale(xCut), y1: M.top,
    x2: xScale(xCut), y2: H - M.bottom,
  }));
  if (!sensDegenerate) {
    svg.appendChild(make("line", {
      class: "gridline",
      x1: M.left, y1: yScale(yCut),
      x2: W - M.right, y2: yScale(yCut),
    }));
  }

  // Axis ticks + labels
  const xTicks = [0, xMax / 2, xMax];
  for (const t of xTicks) {
    const lbl = make("text", {
      x: xScale(t), y: H - M.bottom + 16,
      "text-anchor": "middle", "font-size": "10",
    });
    lbl.textContent = t.toFixed(2);
    svg.appendChild(lbl);
  }
  // Y ticks. When non-degenerate, full 0/0.5/1.0 ticks. When degenerate,
  // a single tick at the centerline showing the constant sensitivity value
  // — labels what the points actually represent.
  if (sensDegenerate) {
    const constSens = sensVals[0] || 0;
    const lbl = make("text", {
      x: M.left - 8, y: yScale(constSens) + 4,
      "text-anchor": "end", "font-size": "10",
    });
    lbl.textContent = constSens.toFixed(2);
    svg.appendChild(lbl);
  } else {
    const yTicks = [0, 0.5, 1.0];
    for (const t of yTicks) {
      const lbl = make("text", {
        x: M.left - 8, y: yScale(t) + 4,
        "text-anchor": "end", "font-size": "10",
      });
      lbl.textContent = t.toFixed(1);
      svg.appendChild(lbl);
    }
  }

  // X-axis label is always present
  const xLab = make("text", {
    class: "axis-label",
    x: M.left + innerW / 2, y: H - 16,
    "text-anchor": "middle",
  });
  xLab.textContent = "Absolute disagreement |Δ|";
  svg.appendChild(xLab);

  // Y-axis label is always present — title only, the tick(s) above
  // describe what values the axis carries.
  const yLab = make("text", {
    class: "axis-label",
    transform: `translate(16, ${M.top + innerH / 2}) rotate(-90)`,
    "text-anchor": "middle",
  });
  yLab.textContent = "Structural sensitivity";
  svg.appendChild(yLab);

  // Degenerate-Y note — printed inside the plot when every branch has
  // the same structural sensitivity (typical for wargames with
  // independent branches). Without this note the row of points along
  // the bottom reads as a layout bug rather than a property of the data.
  if (sensDegenerate) {
    const constSens = sensVals[0] || 0;
    const note = make("text", {
      x: M.left + innerW / 2,
      y: M.top + 16,
      "text-anchor": "middle",
      "font-size": "11",
      "font-style": "italic",
    });
    note.textContent =
      `All strict-panel branches have sensitivity = ${constSens.toFixed(2)} ` +
      `— no structural dependencies, chart reduces to a 1D ranking.`;
    svg.appendChild(note);
  }

  // In degenerate mode, all points share a Y so labels collide unless we
  // alternate them above and below the centerline. Sort by X so the
  // alternation gives adjacent-X points opposite-side labels.
  const labelSide = {};
  if (sensDegenerate) {
    [...audit.strict_panel]
      .sort((a, b) => a.abs_delta - b.abs_delta)
      .forEach((d, i) => { labelSide[d.branch_id] = (i % 2 === 0) ? -1 : 1; });
  }

  // Points
  for (const d of audit.strict_panel) {
    const isAction = actionSet.has(d.branch_id);
    const cx = xScale(d.abs_delta);
    const cy = yScale(sensByBranch[d.branch_id] || 0);

    const a = make("a", {
      href: `/wargame/${encodeURIComponent(namespace)}/branch/${encodeURIComponent(d.branch_id)}`,
      style: "cursor: pointer;",
    });

    a.appendChild(make("circle", {
      class: "point" + (isAction ? " action" : ""),
      cx, cy, r: 5,
    }));

    const title = make("title", {});
    title.textContent =
      `${d.branch_id} — wargame ${d.wargame_probability.toFixed(2)} ` +
      `vs market ${d.market_price_compared.toFixed(2)}`;
    a.appendChild(title);

    const labelText = d.branch_id.length > 32
      ? d.branch_id.slice(0, 30) + "…"
      : d.branch_id;
    let labelAttrs;
    if (sensDegenerate) {
      // Center-anchor labels above or below the dot, alternating
      const above = labelSide[d.branch_id] === -1;
      labelAttrs = {
        class: "point-label",
        x: cx,
        y: cy + (above ? -10 : 18),
        "text-anchor": "middle",
        "font-family": "ui-monospace, Menlo, monospace",
      };
    } else {
      // Default: right of the dot
      labelAttrs = {
        class: "point-label",
        x: cx + 8,
        y: cy + 3,
        "font-family": "ui-monospace, Menlo, monospace",
      };
    }
    const label = make("text", labelAttrs);
    label.textContent = labelText;
    a.appendChild(label);

    svg.appendChild(a);
  }

  _clear(container);
  container.appendChild(svg);
}
