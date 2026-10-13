/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Sparkline, TrendChart } from "./charts";
import { DetailDrawer } from "./drawer";
import { LEVEL, SEVERITY, fmtAgo, fmtDate, fmtDelta, fmtNum, fmtValue, storageGet, storageSet } from "./utils";

const PREFS_KEY = "dmag_dashboard.prefs";
const REFRESH_MS = 5 * 60 * 1000;

/** Generic DMAG board: renders any `dmag.board.<key>` from the JSON contract of its get_board(). */
export class BoardView extends Component {
    static template = "dmag_dashboard.Board";
    static components = { Sparkline, TrendChart, DetailDrawer };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const action = this.props.action || {};
        this.boardKey = action.params?.board || action.context?.board
            || (action.tag === "dmag_dashboard.learning_deploy" ? "learning" : "executive");
        const prefs = storageGet(PREFS_KEY, {});
        this.periods = [
            { value: 7, label: "7 ngày" },
            { value: 30, label: "30 ngày" },
            { value: 90, label: "Quý" },
            { value: 365, label: "12 tháng" },
        ];
        this.state = useState({
            loading: true,
            refreshing: false,
            data: null,
            error: null,
            period: prefs.period || 30,
            filterId: (prefs.filters || {})[this.boardKey] || 0,
            trend: (prefs.trends || {})[this.boardKey] || {},
            expanded: {},
            allSignals: false,
            glossary: false,
            drawer: null,
        });
        Object.assign(this, { LEVEL, SEVERITY, fmtAgo, fmtDelta, fmtNum });
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => this.load(true), REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    get model() {
        return `dmag.board.${this.boardKey}`;
    }

    get params() {
        return { period: this.state.period, filter_id: this.state.filterId };
    }

    async load(silent = false) {
        this.state[silent || this.state.data ? "refreshing" : "loading"] = true;
        try {
            this.state.data = await this.orm.call(this.model, "get_board", [this.params]);
            this.state.error = null;
        } catch (e) {
            this.state.error = e.data?.message || e.message || String(e);
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    savePrefs() {
        const prefs = storageGet(PREFS_KEY, {});
        prefs.period = this.state.period;
        prefs.filters = { ...(prefs.filters || {}), [this.boardKey]: this.state.filterId };
        prefs.trends = { ...(prefs.trends || {}), [this.boardKey]: this.state.trend };
        storageSet(PREFS_KEY, prefs);
    }

    setPeriod(value) {
        this.state.period = value;
        this.savePrefs();
        this.load();
    }

    setFilter(ev) {
        this.state.filterId = parseInt(ev.target.value) || 0;
        this.savePrefs();
        this.load();
    }

    setTrend(panel, key) {
        this.state.trend[panel.id] = key;
        this.savePrefs();
    }

    // ---- navigation & drill-down -------------------------------------------
    openBoard(item) {
        if (item.action && item.key !== this.boardKey) {
            this.action.doAction(item.action);
        }
    }

    async drill(spec) {
        if (!spec) {
            return;
        }
        if (spec.type === "detail") {
            this.state.drawer = { board: spec.board || this.boardKey, kind: spec.kind, id: spec.id };
            return;
        }
        if (spec.type === "ids" && !(spec.ids || []).length) {
            return;
        }
        const action = await this.orm.call(this.model, "action_drill", [spec, this.params]);
        this.action.doAction(action);
    }

    drillKpi(kpi) {
        this.drill({ type: "kpi", key: kpi.key, board: kpi.board });
    }

    drillSignal(sig) {
        const spec = sig.drill || (sig.metric ? { type: "kpi", key: sig.metric } : null);
        if (spec) {
            this.drill({ board: sig.board, ...spec });
        }
    }

    closeDrawer() {
        this.state.drawer = null;
    }

    // ---- formatting ----------------------------------------------------------
    fmt(v, unit) {
        return fmtValue(v, unit, this.state.data?.currency);
    }

    fmtSub(sub) {
        if (!sub) {
            return "";
        }
        if (typeof sub === "string") {
            return sub;
        }
        return `${sub.prefix || ""}${this.fmt(sub.money, "money")}`;
    }

    get updatedAt() {
        const iso = this.state.data?.generated_at;
        if (!iso) {
            return "";
        }
        const d = new Date(iso.replace(" ", "T") + "Z");
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }

    get periodLabel() {
        const p = this.state.data?.period || this.state.period;
        return p === 365 ? "12 tháng trước" : `${p} ngày trước`;
    }

    get signals() {
        const all = this.state.data.signals;
        return this.state.allSignals ? all : all.slice(0, 4);
    }

    deltaIcon(delta) {
        if (!delta) {
            return "fa-minus";
        }
        return delta > 0 ? "fa-arrow-up" : "fa-arrow-down";
    }

    // ---- panels --------------------------------------------------------------
    gridStyle(row) {
        return `grid-template-columns: ${row.widths.map((w) => `minmax(0, ${w}fr)`).join(" ")}`;
    }

    trendOf(panel) {
        const key = panel.series[this.state.trend[panel.id]] ? this.state.trend[panel.id] : panel.default;
        const s = panel.series[key];
        const sum = (arr) => arr.filter((v) => v !== null && v !== undefined).reduce((a, b) => a + b, 0);
        const additive = s.kind === "flow" || !s.kind;
        return {
            key,
            ...s,
            total: additive ? this.fmt(sum(s.current), s.unit) : null,
            prevTotal: additive ? this.fmt(sum(s.previous), s.unit) : null,
            monthly: this.state.data.period > 186,
        };
    }

    funnelMax(panel) {
        return Math.max(1, ...panel.stages.map((s) => s.count));
    }

    funnelConversion(panel, index) {
        const first = panel.stages[0]?.count;
        if (!index || !first || panel.value_unit || panel.distribution) {
            return "";
        }
        return `${Math.round((100 * panel.stages[index].count) / first)}%`;
    }

    barsMax(panel) {
        return Math.max(1, ...panel.items.map((i) => Math.abs(i.value)));
    }

    agingTotal(panel) {
        return panel.buckets.reduce((a, b) => a + b.value, 0);
    }

    rowsOf(panel) {
        const rows = panel.rows || [];
        return this.state.expanded[panel.id] || !panel.limit ? rows : rows.slice(0, panel.limit);
    }

    toggleRows(panel) {
        this.state.expanded[panel.id] = !this.state.expanded[panel.id];
    }

    cell(row, col) {
        const c = row.cells[col.key];
        return c !== null && typeof c === "object" && !Array.isArray(c) ? c : { v: c };
    }

    cellText(c, col) {
        const v = c.v;
        switch (col.type) {
            case "num":
                return v === null || v === undefined ? "—" : fmtNum(v);
            case "money":
                return this.fmt(v, "money");
            case "pct":
                return v === null || v === undefined ? "—" : `${v}%`;
            case "days":
                return v === null || v === undefined ? "—" : `${fmtNum(v)} ngày`;
            case "hours":
                return v === null || v === undefined ? "—" : `${v} giờ`;
            case "ago":
                return fmtAgo(v);
            case "date":
                return v ? fmtDate(v, true) : "—";
            default:
                return v === null || v === undefined || v === false ? "—" : String(v);
        }
    }

    initial(name) {
        const parts = String(name || "?").trim().split(/\s+/);
        return parts[parts.length - 1][0] || "?";
    }
}

registry.category("actions").add("dmag_dashboard.board", BoardView);
// backward compatibility with the first release's client action tag
registry.category("actions").add("dmag_dashboard.learning_deploy", BoardView);
