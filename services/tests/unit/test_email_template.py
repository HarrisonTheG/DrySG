from drysg.alerting.email_template import render_alert
from drysg.core.models import Band
from tests.factories import make_alert


def test_rain_email_leads_with_the_reason_and_omits_the_score():
    # The drying score ignores rain, so it can be high while it rains.
    email = render_alert(make_alert(new_score=82), "https://drysg.example/")
    assert email.subject == "DrySG: rain nearby, bring your laundry in (Bishan)"
    stop = "https://drysg.example/stop?session=11111111-1111-4111-8111-111111111111"
    assert stop in email.text
    assert stop in email.html
    assert "Rain detected" in email.text
    for body in (email.text, email.html):
        assert "82" not in body and "score" not in body


def test_do_not_hang_without_rain_shows_the_score():
    email = render_alert(
        make_alert(new_veto=False, reason="Drying conditions dropped"), "https://drysg.example/"
    )
    assert email.subject == "DrySG: bring your laundry in (Bishan)"
    assert "score 30/100, was 85 Hang Now" in email.text
    assert "Hang Now &middot; 85" in email.html and "Do Not Hang &middot; 30" in email.html


def test_wait_email_and_html_escaping():
    email = render_alert(
        make_alert(new_band=Band.WAIT, new_score=60, new_veto=False, reason="<b>x</b>"),
        "https://drysg.example",
    )
    assert email.subject == "DrySG: drying conditions worsened (Bishan)"
    assert "&lt;b&gt;x&lt;/b&gt;" in email.html


def test_alert_shows_the_check_time_in_sgt_and_the_band_change():
    email = render_alert(make_alert(new_veto=False), "https://drysg.example/")
    assert "Checked: 2:00 pm SGT" in email.text
    assert "2:00 pm SGT" in email.html
    assert "Hang Now &middot; 85" in email.html and "Do Not Hang &middot; 30" in email.html
    assert "12 hours" in email.html


def test_html_emails_are_full_documents_with_a_button_link():
    email = render_alert(make_alert(), "https://drysg.example/")
    assert email.html.startswith("<!doctype html>")
    assert 'href="https://drysg.example/stop?session=' in email.html
    assert "I&#x27;ve brought my laundry in" in email.html
