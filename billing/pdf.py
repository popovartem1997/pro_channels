"""
Генерация PDF для счетов и актов через WeasyPrint.

Использование:
    from billing.pdf import generate_invoice_pdf, generate_act_pdf
    generate_invoice_pdf(invoice)  # сохраняет PDF в invoice.pdf_file
    generate_act_pdf(act)          # сохраняет PDF в act.pdf_file
"""
import logging
from io import BytesIO
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.conf import settings

logger = logging.getLogger(__name__)


def _render_pdf(html_string):
    """Рендерит HTML в PDF через WeasyPrint. Возвращает bytes."""
    from weasyprint import HTML
    pdf_buffer = BytesIO()
    HTML(string=html_string, base_url=str(settings.BASE_DIR)).write_pdf(pdf_buffer)
    return pdf_buffer.getvalue()


def generate_invoice_pdf(invoice):
    """Генерирует PDF счёта и сохраняет в invoice.pdf_file."""
    context = {
        'invoice': invoice,
        'site_name': settings.SITE_NAME,
        'site_url': settings.SITE_URL,
    }
    html = render_to_string('billing/invoice_pdf.html', context)
    pdf_bytes = _render_pdf(html)
    filename = f'invoice_{invoice.number}.pdf'
    invoice.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)
    logger.info(f'PDF счёта {invoice.number} сгенерирован')
    return invoice.pdf_file


def generate_act_pdf(act):
    """Генерирует PDF акта и сохраняет в act.pdf_file."""
    if act.order_id:
        order = act.order
        advertiser = order.advertiser
        order_title = order.title
    elif act.ad_application_id:
        order = None
        app = act.ad_application
        advertiser = app.advertiser
        order_title = f'Заявка #{app.pk} — {app.channel.name}'
    else:
        raise ValueError('Act: не указан ни order, ни ad_application')
    context = {
        'act': act,
        'order': order,
        'order_title': order_title,
        'advertiser': advertiser,
        'ad_application': act.ad_application if act.ad_application_id else None,
        'site_name': settings.SITE_NAME,
        'site_url': settings.SITE_URL,
    }
    html = render_to_string('advertisers/act_pdf.html', context)
    pdf_bytes = _render_pdf(html)
    filename = f'act_{act.number}.pdf'
    act.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)
    logger.info(f'PDF акта {act.number} сгенерирован')
    return act.pdf_file
