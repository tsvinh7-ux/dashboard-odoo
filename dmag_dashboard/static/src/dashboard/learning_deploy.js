/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Sparkline, TrendChart } from "./charts";
import { DetailDrawer } from "./drawer";
import {
    LEVEL,
    SEVERITY,
    fmtAgo,
    fmtCompact,
    fmtDelta,
    fmtMetric,
    fmtNum,
    fmtNum1,
    fmtPct,
    fmtTime,
    storageGet,
    storageSet,
} from "./utils";

const PREFS_KEY = "dmag_dashboard.learning_deploy.prefs";
const REFRESH_MS = 5 * 60 * 1000;

export class LearningDeployDashboard extends Component {
    static template = "dmag_dashboard.LearningDeploy";
    static components = { Sparkline, TrendChart, DetailDrawer };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
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
            userId: prefs.userId || 0,
            trendKey: prefs.trendKey || "lessons_done",
            drawer: null,
            allCustomers: false,
            allSignals: false,
            glossary: false,
        });
        Object.assign(this, { LEVEL, SEVERITY, fmtAgo, fmtCompact, fmtDelta, fmtMetric, fmtNum, fmtPct });
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => this.load(true), REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    get params() {
        return { period: this.state.period, user_id: this.state.userId };
    }

    async load(silent = false) {
        this.state[silent || this.state.data ? "refreshing" : "loading"] = true;
        try {
            this.state.data = await this.orm.call("dmag.dashboard", "get_dashboard_data", [this.params]);
            this.state.error = null;
        } catch (e) {
            this.state.error = e.data?.message || e.message || String(e);
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    savePrefs() {
        storageSet(PREFS_KEY, {
            period: this.state.period,
            userId: this.state.userId,
            trendKey: this.state.trendKey,
        });
    }

    setPeriod(value) {
        this.state.period = value;
        this.savePrefs();
        this.load();
    }

    setUser(ev) {
        this.state.userId = parseInt(ev.target.value) || 0;
        this.savePrefs();
        this.load();
    }

    setTrend(key) {
        this.state.trendKey = key;
        this.savePrefs();
    }

    // ---- drill-down ------------------------------------------------------
    async drill(spec) {
        const action = await this.orm.call("dmag.dashboard", "action_drill", [spec, this.params]);
        this.action.doAction(action);
    }

    drillKpi(key) {
        this.drill({ type: "kpi", key });
    }

    drillFunnel(stage) {
        if (!stage.count) {
            return;
        }
        this.drill({ type: "ids", model: "res.partner", ids: stage.ids, title: `Khách hàng · ${stage.label}` });
    }

    onSignal(sig) {
        if (sig.customer_id) {
            this.openCustomer(sig.customer_id);
        } else if (sig.ids) {
            this.drill({ type: "ids", model: "project.task", ids: sig.ids, title: sig.title });
        } else if (sig.metric) {
            this.drillKpi(sig.metric);
        }
    }

    openCustomer(id) {
        this.state.drawer = { kind: "customer", id };
    }

    openCourse(id) {
        this.state.drawer = { kind: "course", id };
    }

    closeDrawer() {
        this.state.drawer = null;
    }

    // ---- derived view-model -------------------------------------------------
    get updatedAt() {
        return fmtTime(this.state.data?.generated_at);
    }

    get periodLabel() {
        const p = this.state.data?.period || this.state.period;
        return p === 365 ? "12 tháng trước" : `${p} ngày trước`;
    }

    get trend() {
        const d = this.state.data;
        const t = d.trends[this.state.trendKey];
        const cur = t.current.filter((v) => v !== null).reduce((a, b) => a + b, 0);
        const prev = t.previous.filter((v) => v !== null).reduce((a, b) => a + b, 0);
        return { ...t, total: cur, prevTotal: prev, monthly: d.period > 186 };
    }

    get trendTabs() {
        const d = this.state.data;
        return [
            { group: "Học", keys: ["lessons_done", "enrollments", "course_done", "stalled"] },
            { group: "Triển khai", keys: ["tasks_done", "tasks_created", "overdue"] },
        ].map((g) => ({ ...g, items: g.keys.map((k) => ({ key: k, label: d.trends[k].label })) }));
    }

    get customers() {
        const rows = this.state.data.customers;
        return this.state.allCustomers ? rows : rows.slice(0, 8);
    }

    get maxFunnel() {
        return Math.max(1, ...this.state.data.funnel.stages.map((s) => s.count));
    }

    get signals() {
        const all = this.state.data.signals;
        return this.state.allSignals ? all : all.slice(0, 4);
    }

    fmtDays(v) {
        return v === null || v === undefined ? "—" : `${fmtNum1(v)} ngày`;
    }

    drillWaiting() {
        const ids = this.state.data.funnel.speed.waiting_kickoff;
        if (ids.length) {
            this.drill({ type: "ids", model: "res.partner", ids, title: "Khách đã ký, chờ kick-off > 7 ngày" });
        }
    }

    funnelConversion(index) {
        const f = this.state.data.funnel.stages;
        if (!index || !f[0].count) {
            return null;
        }
        return Math.round((100 * f[index].count) / f[0].count);
    }

    kpiValue(kpi) {
        if (kpi.key === "contracts") {
            return fmtNum(kpi.value);
        }
        return fmtMetric(kpi.value, kpi.unit);
    }

    kpiSub(kpi) {
        if (kpi.key === "contracts") {
            const c = this.state.data.currency;
            return `Giá trị ${fmtCompact(kpi.sub.amount)} ${c.symbol}`;
        }
        return kpi.sub.text || "";
    }

    deltaIcon(kpi) {
        if (!kpi.delta) {
            return "fa-minus";
        }
        return kpi.delta > 0 ? "fa-arrow-up" : "fa-arrow-down";
    }
}

registry.category("actions").add("dmag_dashboard.learning_deploy", LearningDeployDashboard);
