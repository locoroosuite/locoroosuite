import pytest

from app.modules.mail.utils.sanitize import sanitize_html, wrap_email_html
from app.modules.mail.controllers.helpers import _rewrite_cid_urls


MARTIAN_LOGIC_EMAIL = """
<table style='width:100%; max-width:475px' border='0' align='center' cellpadding='0' cellspacing='0'>
  <tr>
    <td width="550" align="center" valign="middle" bgcolor="#ffffff">
      <table cellpadding="10">
        <tr>
          <td bgcolor="#37b056">
            <a href="https://example.com/reply" bgcolor="#37b056"
               style="font-family:'Helvetica Neue',Arial,sans-serif;font-size:15px;
                      text-align:center;text-decoration:none;color:#ffffff;
                      word-wrap:break-word;text-transform:uppercase;"
               target="_blank">Reply to Paris</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td style='border-radius:5px;border:1px solid #e8e8e8;padding:10px;background-color:#e8e8e8;'>
      Hi Ruben, thank you for your interest...
    </td>
  </tr>
  <tr>
    <td width="550" align="center" valign="middle" bgcolor="#e8e8e8">
      <table cellpadding="10">
        <tr>
          <td bgcolor="#37b056">
            <a href="https://example.com/reply2" bgcolor="#37b056"
               style="font-family:'Helvetica Neue',Arial,sans-serif;font-size:15px;
                      text-align:center;text-decoration:none;color:#ffffff;
                      word-wrap:break-word;text-transform:uppercase;"
               target="_blank">Reply to Paris</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td>
      <table style='background-color:#202632;' width='100%'>
        <tr>
          <td style='padding:5px;'>
            <a href='https://example.com/'><img alt='ML' src='https://example.com/logo.jpg'
               width='70px' height='35px' border='0' /></a>
          </td>
          <td style='color:#fff;font-size:10px;padding:2px;text-align:center;padding-right:70px'>
            <p>Martian Logic is a CLOUD Recruitment &amp; Onboarding Software.</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


class TestEmailButtonRendering:
    def test_bgcolor_preserved_on_td(self):
        html = '<table><tr><td bgcolor="#37b056"><a href="#" style="color:#fff;">Button</a></td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'bgcolor="#37b056"' in result

    def test_bgcolor_preserved_on_table(self):
        html = '<table bgcolor="#f5f5f5"><tr><td>Content</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'bgcolor="#f5f5f5"' in result

    def test_bgcolor_preserved_on_tr(self):
        html = '<table><tr bgcolor="#eeeeee"><td>Row</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'bgcolor="#eeeeee"' in result

    def test_email_button_visible_with_bgcolor(self):
        html = (
            '<table cellpadding="10"><tr>'
            '<td bgcolor="#37b056">'
            '<a href="#" style="color:#ffffff;text-decoration:none;">REPLY</a>'
            '</td></tr></table>'
        )
        result = sanitize_html(html, allow_images=True)
        assert 'bgcolor="#37b056"' in result
        assert "color:#ffffff" in result
        assert "REPLY" in result

    def test_martian_logic_buttons_visible(self):
        result = sanitize_html(MARTIAN_LOGIC_EMAIL, allow_images=True)
        assert result.count("bgcolor") >= 4
        assert result.count("Reply to Paris") == 2
        assert "#37b056" in result


class TestTableLayoutAttributes:
    def test_width_preserved_on_table(self):
        html = '<table width="600"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'width="600"' in result

    def test_width_preserved_on_td(self):
        html = '<table><tr><td width="300">Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'width="300"' in result

    def test_cellpadding_preserved(self):
        html = '<table cellpadding="10"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'cellpadding="10"' in result

    def test_cellspacing_preserved(self):
        html = '<table cellspacing="0"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'cellspacing="0"' in result

    def test_border_preserved_on_table(self):
        html = '<table border="0"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'border="0"' in result

    def test_valign_preserved_on_td(self):
        html = '<table><tr><td valign="middle">Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'valign="middle"' in result

    def test_align_preserved_on_table(self):
        html = '<table align="center"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'align="center"' in result

    def test_align_preserved_on_td(self):
        html = '<table><tr><td align="center">Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert 'align="center"' in result

    def test_footer_table_width_preserved(self):
        result = sanitize_html(MARTIAN_LOGIC_EMAIL, allow_images=True)
        assert 'width="100%"' in result


class TestEmailCSSProperties:
    def test_border_spacing_preserved(self):
        html = '<table style="border-spacing:0;"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert "border-spacing:0" in result or "border-spacing: 0" in result

    def test_border_collapse_preserved(self):
        html = '<table style="border-collapse:separate;"><tr><td>Cell</td></tr></table>'
        result = sanitize_html(html, allow_images=True)
        assert "border-collapse:separate" in result or "border-collapse: separate" in result

    def test_text_transform_preserved(self):
        html = '<a href="#" style="text-transform:uppercase;">Button</a>'
        result = sanitize_html(html, allow_images=True)
        assert "text-transform:uppercase" in result

    def test_word_wrap_preserved(self):
        html = '<a href="#" style="word-wrap:break-word;">Long text</a>'
        result = sanitize_html(html, allow_images=True)
        assert "word-wrap:break-word" in result

    def test_overflow_wrap_preserved(self):
        html = '<a href="#" style="overflow-wrap:break-word;">Long text</a>'
        result = sanitize_html(html, allow_images=True)
        assert "overflow-wrap:break-word" in result


class TestImageAltTextPreservation:
    def test_alt_text_preserved_when_images_blocked(self):
        html = '<img alt="REPLY TO PARIS" src="https://example.com/btn.png" />'
        result = sanitize_html(html, allow_images=False)
        assert "REPLY TO PARIS" in result
        assert "<img" not in result

    def test_alt_text_single_quoted(self):
        html = "<img alt='Click here' src='https://example.com/btn.png' />"
        result = sanitize_html(html, allow_images=False)
        assert "Click here" in result
        assert "<img" not in result

    def test_alt_text_preserved_when_not_first_attr(self):
        html = '<img src="https://example.com/btn.png" alt="Submit" width="200" />'
        result = sanitize_html(html, allow_images=False)
        assert "Submit" in result
        assert "<img" not in result

    def test_empty_alt_produces_no_text(self):
        html = '<img alt="" src="https://example.com/spacer.png" />'
        result = sanitize_html(html, allow_images=False)
        assert "<img" not in result
        assert result.strip() == ""

    def test_no_alt_produces_no_text(self):
        html = '<img src="https://example.com/spacer.png" />'
        result = sanitize_html(html, allow_images=False)
        assert "<img" not in result
        assert result.strip() == ""

    def test_alt_preserved_inside_link(self):
        html = '<a href="#"><img alt="View offer" src="btn.png" /></a>'
        result = sanitize_html(html, allow_images=False)
        assert "View offer" in result
        assert '<a href="#">' in result
        assert "<img" not in result

    def test_img_kept_when_images_allowed(self):
        html = '<img alt="Photo" src="https://example.com/photo.jpg" />'
        result = sanitize_html(html, allow_images=True)
        assert "<img" in result
        assert 'alt="Photo"' in result

    def test_footer_logo_alt_preserved(self):
        result = sanitize_html(MARTIAN_LOGIC_EMAIL, allow_images=False)
        assert "ML" in result


class TestWrapperNoForcedBorderCollapse:
    def test_wrapper_does_not_force_border_collapse(self):
        wrapped = wrap_email_html("<p>test</p>")
        assert "table{border-collapse:collapse;}" not in wrapped

    def test_wrapper_has_basic_styles(self):
        wrapped = wrap_email_html("<p>test</p>")
        assert "margin:0;padding:0" in wrapped
        assert "max-width:720px" in wrapped


class TestRegressionMartianLogicEmail:
    def test_full_email_buttons_and_footer_with_images_blocked(self):
        result = sanitize_html(MARTIAN_LOGIC_EMAIL, allow_images=False)
        assert result.count("Reply to Paris") == 2
        assert "#37b056" in result
        assert "#202632" in result
        assert "Martian Logic is a CLOUD Recruitment" in result
        assert "ML" in result
        assert "<img" not in result

    def test_full_email_buttons_and_footer_with_images_allowed(self):
        result = sanitize_html(MARTIAN_LOGIC_EMAIL, allow_images=True)
        assert result.count("Reply to Paris") == 2
        assert "#37b056" in result
        assert "#202632" in result
        assert "Martian Logic is a CLOUD Recruitment" in result
        assert "<img" in result
        assert 'width="100%"' in result
        assert 'cellpadding="10"' in result

    def test_button_white_text_on_green_background(self):
        html = (
            '<table><tr><td bgcolor="#37b056">'
            '<a href="#" style="color:#ffffff;text-transform:uppercase;">REPLY</a>'
            '</td></tr></table>'
        )
        result = sanitize_html(html, allow_images=True)
        assert 'bgcolor="#37b056"' in result
        assert "color:#ffffff" in result
        assert "text-transform:uppercase" in result
        assert "REPLY" in result

    def test_consecutive_tables_no_forced_collapse(self):
        html = (
            '<table style="border-collapse:separate;border-spacing:0;width:600px;">'
            '<tr><td style="padding:20px;">Main content</td></tr></table>'
            '<table style="border-collapse:separate;border-spacing:0;width:600px;background-color:#f5f5f5;">'
            '<tr><td style="padding:10px;">Footer text</td></tr></table>'
        )
        result = sanitize_html(html, allow_images=True)
        assert "border-collapse:separate" in result
        assert "border-spacing:0" in result
        wrapped = wrap_email_html(result)
        assert "table{border-collapse:collapse;}" not in wrapped


class TestCIDInlineImages:
    def test_cid_image_preserved_when_images_blocked(self):
        html = '<p>Hello</p><img src="cid:image001.png@01DCED0F.C6C76180" alt="Logo">'
        result = sanitize_html(html, allow_images=False)
        assert "<img" in result
        assert 'cid:image001.png@01DCED0F.C6C76180' in result

    def test_cid_image_without_alt_preserved_when_images_blocked(self):
        html = '<p>Hello</p><img src="cid:image001.png@01DCED0F.C6C76180">'
        result = sanitize_html(html, allow_images=False)
        assert "<img" in result
        assert 'cid:image001.png@01DCED0F.C6C76180' in result

    def test_external_image_removed_when_cid_present(self):
        html = (
            '<img src="cid:inline-logo.png" alt="Logo">'
            '<img src="https://tracker.example.com/pixel.png" alt="Track">'
        )
        result = sanitize_html(html, allow_images=False)
        assert "<img" in result
        assert "cid:inline-logo.png" in result
        assert "tracker.example.com" not in result
        assert "Track" in result

    def test_cid_image_preserved_when_images_allowed(self):
        html = '<img src="cid:image001.png@01DCED0F.C6C76180" alt="Photo">'
        result = sanitize_html(html, allow_images=True)
        assert "<img" in result
        assert "cid:image001.png@01DCED0F.C6C76180" in result

    def test_multiple_cid_images_preserved(self):
        html = (
            '<img src="cid:img1@domain">'
            '<img src="cid:img2@domain">'
            '<img src="https://example.com/external.png">'
        )
        result = sanitize_html(html, allow_images=False)
        assert result.count("<img") == 2
        assert "cid:img1@domain" in result
        assert "cid:img2@domain" in result
        assert "example.com" not in result

    def test_cid_with_other_attributes_preserved(self):
        html = '<img src="cid:chart.png@corp" width="600" height="400" alt="Chart">'
        result = sanitize_html(html, allow_images=False)
        assert "<img" in result
        assert "cid:chart.png@corp" in result

    def test_no_images_cid_map_empty(self):
        html = "<p>No images here</p>"
        result = sanitize_html(html, allow_images=False)
        assert "<img" not in result
        assert "No images here" in result


class TestRewriteCIDUrls:
    def test_cid_replaced_with_attachment_url(self, app):
        with app.test_request_context("/"):
            html = '<img src="cid:image001.png@01DCED0F.C6C76180">'
            cid_map = {"image001.png@01DCED0F.C6C76180": 2}
            result = _rewrite_cid_urls(html, cid_map, account_id=3, message_id=100)
        assert "cid:" not in result
        assert "/mail/message/3/100/attachment/2?inline=1" in result

    def test_multiple_cids_replaced(self, app):
        with app.test_request_context("/"):
            html = '<img src="cid:img1@x"> <img src="cid:img2@x">'
            cid_map = {"img1@x": 0, "img2@x": 1}
            result = _rewrite_cid_urls(html, cid_map, account_id=1, message_id=5)
        assert "attachment/0?inline=1" in result
        assert "attachment/1?inline=1" in result
        assert "cid:" not in result

    def test_unknown_cid_left_unchanged(self, app):
        with app.test_request_context("/"):
            html = '<img src="cid:unknown@x">'
            cid_map = {"known@x": 0}
            result = _rewrite_cid_urls(html, cid_map, account_id=1, message_id=1)
        assert 'src="cid:unknown@x"' in result

    def test_empty_cid_map_returns_html_unchanged(self, app):
        html = '<img src="cid:img@x">'
        result = _rewrite_cid_urls(html, {}, account_id=1, message_id=1)
        assert result == html

    def test_empty_html_returns_empty(self, app):
        result = _rewrite_cid_urls("", {"img@x": 0}, account_id=1, message_id=1)
        assert result == ""

    def test_single_quoted_cid_replaced(self, app):
        with app.test_request_context("/"):
            html = "<img src='cid:logo@corp'>"
            cid_map = {"logo@corp": 3}
            result = _rewrite_cid_urls(html, cid_map, account_id=2, message_id=10)
        assert "cid:" not in result
        assert "attachment/3?inline=1" in result
