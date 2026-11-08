"""EVT.1: `status` is not editable, and `migrate events zero` removes the event pages.

`0001_initial` is schema-only, so reversing it dropped the events tables and left the index and
event rows in `wagtailcore_page` (orphans of a content type with no table); the next `migrate`
then failed to add an index on the taken slug. The reverse step here deletes those pages with
their subtrees, revisions, workflow states, log entries and references, and repairs the
parents' `numchild`, before `0001` drops the tables. Forwards it does nothing: `post_migrate`
adds the indexes.
"""

from typing import Any

from django.db import migrations, models
from django.db.models import Q

STEPLEN = 4  # treebeard's path step


def remove_event_pages(apps: Any, schema_editor: Any) -> None:
    ContentType = apps.get_model("contenttypes.ContentType")
    Page = apps.get_model("wagtailcore.Page")

    event_types = ContentType.objects.filter(app_label="events")
    subtrees = Q()
    for path in Page.objects.filter(content_type__in=event_types).values_list("path", flat=True):
        subtrees |= Q(path__startswith=path)
    if not subtrees:
        return
    doomed = Page.objects.filter(subtrees)
    ids = list(doomed.values_list("pk", flat=True))
    object_ids = [str(pk) for pk in ids]
    page_types = set(doomed.values_list("content_type_id", flat=True))
    parent_paths = {path[:-STEPLEN] for path in doomed.values_list("path", flat=True)}

    by_object = Q(content_type_id__in=page_types, object_id__in=object_ids)
    apps.get_model("wagtailcore.Revision").objects.filter(by_object).delete()
    apps.get_model("wagtailcore.WorkflowState").objects.filter(by_object).delete()
    apps.get_model("wagtailcore.ReferenceIndex").objects.filter(
        by_object | Q(to_content_type_id__in=page_types, to_object_id__in=object_ids)
    ).delete()
    apps.get_model("wagtailcore.PageLogEntry").objects.filter(page_id__in=ids).delete()
    Page.objects.filter(alias_of_id__in=ids).update(alias_of=None)
    Page.objects.filter(pk__in=ids).delete()

    for parent in Page.objects.filter(path__in=parent_paths):
        parent.numchild = Page.objects.filter(
            path__startswith=parent.path, depth=parent.depth + 1
        ).count()
        parent.save(update_fields=["numchild"])


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
        ("wagtailcore", "0098_apitoken"),
    ]

    operations = [
        migrations.AlterField(
            model_name="eventpage",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Awaiting review"),
                    ("published", "Published"),
                    ("cancelled", "Cancelled"),
                    ("hidden", "Hidden"),
                ],
                db_index=True,
                default="draft",
                editable=False,
                max_length=16,
                verbose_name="status",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, remove_event_pages),
    ]
