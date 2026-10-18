from email.message import EmailMessage

import pytest
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits


@pytest.mark.parametrize('case,status', [('count', 'skipped_count'), ('depth', 'mime_depth_exceeded'), ('malformed', 'skipped_parse')])
def test_mime_remainder_is_visible_and_never_reported_as_complete(tmp_path, case, status):
    message = EmailMessage()
    message.set_content('ROOT')
    if case == 'count':
        for index in range(1005):
            message.add_attachment(b'opaque', maintype='application', subtype='octet-stream', filename=f'{index}.bin')
        raw = message.as_bytes()
    elif case == 'depth':
        for _ in range(33):
            parent = EmailMessage()
            parent.make_mixed()
            parent.attach(message)
            message = parent
        raw = message.as_bytes()
    else:
        raw = b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=test\r\n\r\n--test\r\nINVALID HEADER\r\n\r\nBODY\r\n--test--\r\n'
    path = tmp_path / 'root.eml'
    path.write_bytes(raw)
    inventory = inspect_attachment_inventory(path, path.name, limits=AssessmentLimits(), traversal=TraversalLimits())
    assert inventory.complete is False
    markers = [n for n in inventory.nodes if n.status == status]
    assert len(markers) == 1
    assert markers[0].assessment == 'unknown'
    assert markers[0].children_complete is False
    assert markers[0].saved_path is None
    selection = recommend_scope(inventory, AssessmentLimits())
    assert selection.requires_review
    assert not next(d for d in selection.decisions if d.source_key == markers[0].source_key).effective_included
    assert len(inventory.nodes) <= 1000
