from odoo import api, fields, models


class SlideChannelPartner(models.Model):
    _inherit = 'slide.channel.partner'

    dmag_completed_date = fields.Datetime(
        'Ngày hoàn thành khóa', compute='_compute_dmag_completed_date', store=True, index=True)

    @api.depends('member_status')
    def _compute_dmag_completed_date(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.member_status == 'completed':
                rec.dmag_completed_date = rec.dmag_completed_date or rec.write_date or now
            else:
                rec.dmag_completed_date = False
