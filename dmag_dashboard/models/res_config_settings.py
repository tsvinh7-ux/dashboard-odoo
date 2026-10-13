from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dmag_spdv_categ_ids = fields.Many2many(related='company_id.dmag_spdv_categ_ids', readonly=False)
    dmag_stall_days = fields.Integer(related='company_id.dmag_stall_days', readonly=False)
    dmag_idle_days = fields.Integer(related='company_id.dmag_idle_days', readonly=False)
    dmag_project_scope = fields.Selection(related='company_id.dmag_project_scope', readonly=False)
