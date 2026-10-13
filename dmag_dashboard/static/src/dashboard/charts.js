/** @odoo-module **/

import { Component, useRef, useState } from "@odoo/owl";
import { fmtCompact, fmtDate, fmtMonth, fmtNum, fmtValue, niceMax } from "./utils";

export class Sparkline extends Component {
    static template = "dmag_dashboard.Sparkline";
    static props = { values: Array, tone: { type: String, optional: true } };

    get geometry() {
        const vals = (this.props.values || []).map((v) => (v === null || v === undefined ? null : v));
        const nums = vals.filter((v) => v !== null);
        const W = 120;
        const H = 32;
        if (nums.length < 2) {
            return null;
        }
        const max = Math.max(...nums);
        const min = Math.min(...nums, 0);
        const span = max - min || 1;
        const step = W / (vals.length - 1);
        const pts = [];
        vals.forEach((v, i) => {
            if (v !== null) {
                pts.push([i * step, H - 3 - ((v - min) / span) * (H - 6)]);
            }
        });
        const line = pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
        const area = `0,${H} ${line} ${pts[pts.length - 1][0].toFixed(1)},${H}`;
        const last = pts[pts.length - 1];
        return { line, area, last, W, H };
    }
}

export class TrendChart extends Component {
    static template = "dmag_dashboard.TrendChart";
    static props = {
        labels: Array,
        current: Array,
        previous: Array,
        unit: { type: String, optional: true },
        monthly: { type: Boolean, optional: true },
        currency: { type: Object, optional: true },
    };

    setup() {
        this.svgRef = useRef("svg");
        this.state = useState({ hover: null });
        this.W = 720;
        this.H = 240;
        this.pad = { l: 44, r: 16, t: 16, b: 30 };
    }

    get model() {
        const { current, previous, labels } = this.props;
        const { W, H, pad } = this;
        const all = [...current, ...previous].filter((v) => v !== null && v !== undefined);
        const max = niceMax(Math.max(0, ...all));
        const n = current.length;
        const iw = W - pad.l - pad.r;
        const ih = H - pad.t - pad.b;
        const x = (i) => pad.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
        const y = (v) => pad.t + ih - (v / max) * ih;
        const path = (vals) => {
            let d = "";
            let pen = false;
            vals.forEach((v, i) => {
                if (v === null || v === undefined) {
                    pen = false;
                    return;
                }
                d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
                pen = true;
            });
            return d;
        };
        const cur = path(current);
        const firstIdx = current.findIndex((v) => v !== null && v !== undefined);
        const lastIdx = current.length - 1 - [...current].reverse().findIndex((v) => v !== null && v !== undefined);
        const area = firstIdx >= 0 && lastIdx >= firstIdx
            ? `${cur}L${x(lastIdx).toFixed(1)},${y(0)}L${x(firstIdx).toFixed(1)},${y(0)}Z`
            : "";
        const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
            y: y(max * f),
            label: this.props.unit === "percent" ? `${Math.round(max * f)}%`
                : this.props.unit === "money" ? fmtCompact(max * f) : fmtNum(max * f),
        }));
        const every = Math.max(1, Math.ceil(n / 7));
        const xlabels = labels
            .map((l, i) => ({ i, x: x(i), label: this.props.monthly ? fmtMonth(l) : fmtDate(l) }))
            .filter((l) => l.i % every === 0 || l.i === n - 1)
            .filter((l, idx, arr) => idx === arr.length - 1 || arr[arr.length - 1].i - l.i >= every * 0.6);
        const end = lastIdx >= 0 ? { x: x(lastIdx), y: y(current[lastIdx]) } : null;
        return { cur, prev: path(previous), area, ticks, xlabels, x, y, n, end, baseline: y(0) };
    }

    onMove(ev) {
        const rect = this.svgRef.el.getBoundingClientRect();
        const px = ((ev.clientX - rect.left) / rect.width) * this.W;
        const { n } = this.model;
        const iw = this.W - this.pad.l - this.pad.r;
        const i = Math.round(((px - this.pad.l) / iw) * (n - 1));
        this.state.hover = Math.max(0, Math.min(n - 1, i));
    }

    onLeave() {
        this.state.hover = null;
    }

    get tooltip() {
        const i = this.state.hover;
        if (i === null) {
            return null;
        }
        const { x, y } = this.model;
        const cur = this.props.current[i];
        const prev = this.props.previous[i];
        const left = (x(i) / this.W) * 100;
        return {
            left: `${left}%`,
            alignRight: left > 70,
            label: this.props.monthly ? fmtMonth(this.props.labels[i]) : fmtDate(this.props.labels[i], true),
            cur: fmtValue(cur, this.props.unit, this.props.currency),
            prev: fmtValue(prev, this.props.unit, this.props.currency),
            lx: x(i),
            cy: cur === null || cur === undefined ? null : y(cur),
            py: prev === null || prev === undefined ? null : y(prev),
        };
    }
}
