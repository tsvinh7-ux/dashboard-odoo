/** @odoo-module **/

import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { LEVEL, fmtValue } from "./utils";

/** Level-2 drill-down: a generic 360° side panel. The backend returns
 *  {eyebrow, title, subtitle, actions, gauge, stats, chips, sections[{title, items}]};
 *  every item opens the native Odoo record (level 3). */
export class DetailDrawer extends Component {
    static template = "dmag_dashboard.DetailDrawer";
    static props = {
        board: String,
        kind: String,
        id: Number,
        params: Object,
        currency: Object,
        onClose: Function,
        onDrill: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: true, data: null, error: null });
        this.LEVEL = LEVEL;
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((next) => {
            if (next.kind !== this.props.kind || next.id !== this.props.id || next.board !== this.props.board) {
                return this.load(next);
            }
        });
    }

    async load(props) {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(`dmag.board.${props.board}`, "get_detail",
                [props.kind, props.id, props.params]);
            this.state.error = null;
        } catch (e) {
            this.state.error = e.data?.message || e.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    fmt(v, unit) {
        return fmtValue(v, unit, this.props.currency);
    }

    itemValue(item) {
        if (!item.value) {
            return "";
        }
        return item.value.text !== undefined ? item.value.text : this.fmt(item.value.v, item.value.unit);
    }

    drill(spec) {
        if (spec) {
            this.props.onDrill({ board: this.props.board, ...spec });
        }
    }
}
