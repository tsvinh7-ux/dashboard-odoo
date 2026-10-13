/** @odoo-module **/

import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ENROLL_STATUS, LEVEL, fmtAgo, fmtCompact, fmtDate, fmtNum } from "./utils";

/** Level-2 drill-down: a 360° side panel for one customer or one course.
 *  Every row inside opens the native Odoo record (level 3). */
export class DetailDrawer extends Component {
    static template = "dmag_dashboard.DetailDrawer";
    static props = {
        kind: String,
        id: Number,
        params: Object,
        onClose: Function,
        onDrill: Function,
        onOpenCustomer: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: true, data: null });
        this.LEVEL = LEVEL;
        this.ENROLL_STATUS = ENROLL_STATUS;
        this.fmtNum = fmtNum;
        this.fmtCompact = fmtCompact;
        this.fmtDate = fmtDate;
        this.fmtAgo = fmtAgo;
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((next) => {
            if (next.kind !== this.props.kind || next.id !== this.props.id) {
                return this.load(next);
            }
        });
    }

    async load(props) {
        this.state.loading = true;
        const method = props.kind === "customer" ? "get_customer_detail" : "get_course_detail";
        this.state.data = await this.orm.call("dmag.dashboard", method, [props.id, props.params]);
        this.state.loading = false;
    }

    open(model, id) {
        this.props.onDrill({ type: "record", model, id });
    }

    onKeydown(ev) {
        if (ev.key === "Escape") {
            this.props.onClose();
        }
    }
}
