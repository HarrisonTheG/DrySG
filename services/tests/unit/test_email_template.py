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
    assert "score 30/100, was 85 Hang Now" in email.html


def test_wait_email_and_html_escaping():
    email = render_alert(
        make_alert(new_band=Band.WAIT, new_score=60, new_veto=False, reason="<b>x</b>"),
        "https://drysg.example",
    )
    assert email.subject == "DrySG: drying conditions worsened (Bishan)"
    assert "&lt;b&gt;x&lt;/b&gt;" in email.html
