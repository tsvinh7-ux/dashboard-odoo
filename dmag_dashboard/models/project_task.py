from odoo import api, fields, models
from odoo.addons.project.models.project_task import CLOSED_STATES


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Odoo 17 does not keep the moment a task is marked done/cancelled (date_end only
    # moves with folded stages), but every throughput / on-time metric needs it.
    dmag_closed_date = fields.Datetime(
        'Ngày đóng', compute='_compute_dmag_closed_date', store=True, index=True, copy=False)

    @api.depends('state')
    def _compute_dmag_closed_date(self):
        now = fields.Datetime.now()
        for task in self:
            if task.state in CLOSED_STATES:
                task.dmag_closed_date = (task.dmag_closed_date or task.date_end
                                         or task.date_last_stage_update or now)
            else:
                task.dmag_closed_date = False
