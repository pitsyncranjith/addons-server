from django.conf.urls import include, patterns, url
from django.views.generic.base import RedirectView

from waffle.decorators import waffle_switch

from . import views


USER_ID = r"""(?P<user_id>[^/<>"']+)"""


def migration_on(fn):
    return waffle_switch('!fxa-migrated')(fn)


# These will all start with /user/<user_id>/
detail_patterns = patterns(
    '',
    url('^$', views.profile, name='users.profile'),
    url('^themes(?:/(?P<category>[^ /]+))?$', views.themes,
        name='users.themes'),
    url('^confirm/resend$', views.confirm_resend, name='users.confirm.resend'),
    url('^confirm/(?P<token>[-\w]+)$', views.confirm, name='users.confirm'),
    url(r'^emailchange/(?P<token>[-\w]+={0,3})/(?P<hash>[\w]+)$',
        views.emailchange, name="users.emailchange"),
    url('^abuse', views.report_abuse, name='users.abuse'),
    url('^rmlocale$', views.remove_locale, name='users.remove-locale'),
)

users_patterns = patterns(
    '',
    url('^ajax$', views.ajax, name='users.ajax'),
    url('^delete$', views.delete, name='users.delete'),
    url('^delete_photo/(?P<user_id>\d+)?$', views.delete_photo,
        name='users.delete_photo'),
    url('^edit$', views.edit, name='users.edit'),
    url('^edit(?:/(?P<user_id>\d+))?$', views.admin_edit,
        name='users.admin_edit'),
    url('^login', views.login, name='users.login'),
    url('^logout', views.logout, name='users.logout'),
    url('^register$',
        RedirectView.as_view(pattern_name='users.login', permanent=True),
        name='users.register'),
    url('^migrate', views.migrate, name='users.migrate'),
    url(r'^unsubscribe/(?P<token>[-\w]+={0,3})/(?P<hash>[\w]+)/'
        r'(?P<perm_setting>[\w]+)?$', views.unsubscribe,
        name="users.unsubscribe"),
)


urlpatterns = patterns(
    '',
    # URLs for a single user.
    ('^user/%s/' % USER_ID, include(detail_patterns)),
    ('^users/', include(users_patterns)),
)
