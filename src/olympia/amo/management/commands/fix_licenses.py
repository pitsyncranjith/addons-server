from django.core.management.base import BaseCommand
from django.utils.translation import activate

from olympia.versions.models import License
from olympia.addons.models import Addon
from olympia.devhub.models import ActivityLog
from olympia.translations.models import Translation


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        activate('en-us')

        mpl11 = License.objects.get(pk=5)

        assert str(mpl11.name) == 'Copyright Jason Savard', 'wrong license'

        # set on_form explicitly to False to make sure it's not
        # visible and set `builtin` to 1 to make sure it's filtered
        # everywhere.
        mpl11.on_form = False
        mpl11.builtin = 1
        mpl11.save()

        search = '"versions.license": 5}'
        changed_addons = ActivityLog.objects.filter(
            action=37,
            _arguments__contains=search)

        msg = 'too many or few add-ons changed, found %s' % len(changed_addons)
        assert len(changed_addons) == 3, msg

        for log in changed_addons:
            license, addon = log.arguments

            assert isinstance(license, License)
            assert isinstance(addon, Addon)

            last_version = addon.get_version()

            # Create new object of broken license since they were supposed
            # to change and show them in the list again
            last_version.license.pk = None
            last_version.license.on_form = True
            last_version.license.save()
            last_version.save()

        # Now we're able to fix the actual strings.
        # Broken are: de, en-us, and fr. We untangled those three above
        # already.
        translations = Translation.objects.filter(
            id=mpl11.name.id,
            locale__in=('en-us', 'fr', 'de'))

        print('updating %s' % translations)

        translations.update(
            localized_string='Mozilla Public License Version 1.1')

        # It's super hard to find correct texts of the license translation
        # so let's be naive and delete broken translation languages
        if mpl11.text:
            translations = Translation.objects.filter(
                id=mpl11.text.id,
                locale__in=('de', 'fr', 'en-us')
            )
            print('deleting %s' % translations)
            translations.delete()
