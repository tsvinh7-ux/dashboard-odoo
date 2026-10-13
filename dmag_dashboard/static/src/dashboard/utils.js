/** @odoo-module **/

const NF = new Intl.NumberFormat("vi-VN");
const NF1 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });

export function fmtNum(v) {
    if (v === null || v === undefined || v === false) {
        return "—";
    }
    return NF.format(Math.round(v));
}

export function fmtNum1(v) {
    return v === null || v === undefined ? "—" : NF1.format(v);
}

export function fmtPct(v) {
    if (v === null || v === undefined || v === false) {
        return "—";
    }
    return `${NF1.format(v)}%`;
}

/** 3.010.000.000 -> "3,01 tỷ"; 69.993.000 -> "70 tr" */
export function fmtCompact(v) {
    if (v === null || v === undefined) {
        return "—";
    }
    const a = Math.abs(v);
    if (a >= 1e9) {
        return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(v / 1e9)} tỷ`;
    }
    if (a >= 1e6) {
        return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(v / 1e6)} tr`;
    }
    if (a >= 1e4) {
        return `${NF1.format(v / 1e3)}k`;
    }
    return NF.format(v);
}

export function fmtMetric(v, unit) {
    return unit === "percent" ? fmtPct(v) : fmtNum(v);
}

/** Format any value by unit; money uses the company currency symbol. */
export function fmtValue(v, unit, currency) {
    if (v === null || v === undefined || v === false) {
        return "—";
    }
    switch (unit) {
        case "money": {
            const sym = currency?.symbol || "";
            return currency?.position === "before" ? `${sym}${fmtCompact(v)}` : `${fmtCompact(v)} ${sym}`.trim();
        }
        case "percent":
            return fmtPct(v);
        case "days":
            return `${NF1.format(v)} ngày`;
        case "hours":
            return `${NF1.format(v)} giờ`;
        default:
            return fmtNum(v);
    }
}

export function fmtDelta(delta, unit) {
    if (delta === null || delta === undefined) {
        return "";
    }
    const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
    const abs = NF1.format(Math.abs(delta));
    return unit === "percent" ? `${sign}${abs} điểm` : `${sign}${abs}%`;
}

function parseUtc(iso) {
    return iso ? new Date(iso.replace(" ", "T") + "Z") : null;
}

export function fmtDate(iso, withYear = false) {
    const d = parseUtc(iso);
    if (!d) {
        return "—";
    }
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    return withYear ? `${dd}/${mm}/${d.getFullYear()}` : `${dd}/${mm}`;
}

export function fmtMonth(iso) {
    const d = parseUtc(iso);
    return d ? `T${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}` : "";
}

export function fmtTime(iso) {
    const d = parseUtc(iso);
    return d ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
}

export function fmtAgo(days) {
    if (days === null || days === undefined) {
        return "—";
    }
    if (days <= 0) {
        return "hôm nay";
    }
    if (days === 1) {
        return "hôm qua";
    }
    if (days < 30) {
        return `${days} ngày trước`;
    }
    return `${Math.round(days / 30)} tháng trước`;
}

/** Axis maximum split into 4 clean steps (1, 2, 5 x 10^n), so every tick is a round number. */
export function niceMax(v) {
    if (!v || v <= 0) {
        return 4;
    }
    const raw = v / 4;
    const exp = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 5, 10]) {
        if (raw <= m * exp) {
            return Math.max(4, m * exp * 4);
        }
    }
    return 40 * exp;
}

export const LEVEL = {
    good: { label: "Tốt", icon: "fa-check-circle" },
    watch: { label: "Cần chú ý", icon: "fa-exclamation-circle" },
    risk: { label: "Nguy cơ", icon: "fa-exclamation-triangle" },
};

export const SEVERITY = {
    critical: { label: "Nghiêm trọng", icon: "fa-exclamation-triangle" },
    warning: { label: "Cần chú ý", icon: "fa-exclamation-circle" },
    positive: { label: "Tín hiệu tốt", icon: "fa-arrow-circle-up" },
    info: { label: "Thông tin", icon: "fa-info-circle" },
};

export const ENROLL_STATUS = {
    joined: "Mới ghi danh",
    ongoing: "Đang học",
    completed: "Hoàn thành",
    invited: "Được mời",
};

export function storageGet(key, fallback) {
    try {
        const raw = window.localStorage.getItem(key);
        return raw === null ? fallback : JSON.parse(raw);
    } catch {
        return fallback;
    }
}

export function storageSet(key, value) {
    try {
        window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // storage unavailable (private mode): preferences simply aren't remembered
    }
}
