# -*- coding: utf-8 -*-
import json

from django.core import mail
from django.core.urlresolvers import reverse

import mock
from pyquery import PyQuery as pq
from rest_framework.exceptions import ParseError
from rest_framework.test import APIRequestFactory

from olympia import amo
from olympia.addons.utils import generate_addon_guid
from olympia.amo import helpers
from olympia.amo.tests import (
    addon_factory, APITestClient, TestCase, MobileTest, version_factory,
    user_factory)
from olympia.access.models import Group, GroupUser
from olympia.addons.models import Addon, AddonUser
from olympia.devhub.models import ActivityLog
from olympia.reviews.views import ReviewViewSet
from olympia.reviews.models import Review, ReviewFlag
from olympia.users.models import UserProfile


class ReviewTest(TestCase):
    fixtures = ['reviews/dev-reply.json', 'base/admin']

    def setUp(self):
        super(ReviewTest, self).setUp()
        self.addon = Addon.objects.get(id=1865)

    def login_dev(self):
        self.client.login(username='trev@adblockplus.org', password='password')

    def login_admin(self):
        self.client.login(username='jbalogh@mozilla.com', password='password')

    def make_it_my_review(self, review_id=218468):
        r = Review.objects.get(id=review_id)
        r.user = UserProfile.objects.get(username='jbalogh')
        r.save()


class TestViews(ReviewTest):

    def test_dev_reply(self):
        url = helpers.url('addons.reviews.detail', self.addon.slug, 218468)
        r = self.client.get(url)
        assert r.status_code == 200

    def test_dev_no_rss(self):
        url = helpers.url('addons.reviews.detail', self.addon.slug, 218468)
        r = self.client.get(url)
        doc = pq(r.content)
        assert doc('link[title=RSS]').length == 0

    def test_404_user_page(self):
        url = helpers.url('addons.reviews.user', self.addon.slug, 233452342)
        r = self.client.get(url)
        assert r.status_code == 404

    def test_feed(self):
        url = helpers.url('addons.reviews.list.rss', self.addon.slug)
        r = self.client.get(url)
        assert r.status_code == 200

    def test_abuse_form(self):
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        self.assertTemplateUsed(r, 'reviews/report_review.html')
        r = self.client.get(helpers.url('addons.reviews.detail',
                                        self.addon.slug, 218468))
        self.assertTemplateUsed(r, 'reviews/report_review.html')

    def test_edit_review_form(self):
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        self.assertTemplateUsed(r, 'reviews/edit_review.html')
        r = self.client.get(helpers.url('addons.reviews.detail',
                                        self.addon.slug, 218468))
        self.assertTemplateUsed(r, 'reviews/edit_review.html')

    def test_list(self):
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        assert r.status_code == 200
        doc = pq(r.content)
        reviews = doc('#reviews .item')
        assert reviews.length == Review.objects.count()
        assert Review.objects.count() == 2
        assert doc('.secondary .average-rating').length == 1
        assert doc('.secondary .no-rating').length == 0

        r = Review.objects.get(id=218207)
        item = reviews.filter('#review-218207')
        assert r.reply_to_id is None
        assert not item.hasClass('reply')
        assert item.length == 1
        assert item.attr('data-rating') == str(r.rating)

        r = Review.objects.get(id=218468)
        item = reviews.filter('#review-218468')
        assert item.length == 1
        assert r.reply_to_id == 218207
        assert item.hasClass('reply')
        assert r.rating is None
        assert item.attr('data-rating') == ''

    def test_list_rss(self):
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        doc = pq(r.content)
        assert doc('link[title=RSS]').length == 1

    def test_empty_list(self):
        Review.objects.all().delete()
        assert Review.objects.count() == 0
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        assert r.status_code == 200
        doc = pq(r.content)
        assert doc('#reviews .item').length == 0
        assert doc('#add-first-review').length == 1
        assert doc('.secondary .average-rating').length == 0
        assert doc('.secondary .no-rating').length == 1

    def test_list_item_actions(self):
        self.login_admin()
        self.make_it_my_review()
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        reviews = pq(r.content)('#reviews .item')

        r = Review.objects.get(id=218207)
        item = reviews.filter('#review-218207')
        actions = item.find('.item-actions')
        assert actions.length == 1
        classes = sorted(c.get('class') for c in actions.find('li a'))
        assert classes == ['delete-review', 'flag-review']

        r = Review.objects.get(id=218468)
        item = reviews.filter('#review-218468')
        actions = item.find('.item-actions')
        assert actions.length == 1
        classes = sorted(c.get('class') for c in actions.find('li a'))
        assert classes == ['delete-review', 'review-reply-edit']

    def test_cant_view_unlisted_addon_reviews(self):
        """An unlisted addon doesn't have reviews."""
        self.addon.update(is_listed=False)
        assert self.client.get(helpers.url('addons.reviews.list',
                                           self.addon.slug)).status_code == 404


class TestFlag(ReviewTest):

    def setUp(self):
        super(TestFlag, self).setUp()
        self.url = helpers.url('addons.reviews.flag', self.addon.slug, 218468)
        self.login_admin()

    def test_no_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        assert response.status_code == 401

    def test_new_flag(self):
        response = self.client.post(self.url, {'flag': ReviewFlag.SPAM})
        assert response.status_code == 200
        assert response.content == (
            '{"msg": "Thanks; this review has been '
            'flagged for editor approval."}')
        assert ReviewFlag.objects.filter(flag=ReviewFlag.SPAM).count() == 1
        assert Review.objects.filter(editorreview=True).count() == 1

    def test_new_flag_mine(self):
        self.make_it_my_review()
        response = self.client.post(self.url, {'flag': ReviewFlag.SPAM})
        assert response.status_code == 403

    def test_flag_review_deleted(self):
        Review.objects.get(pk=218468).delete()
        response = self.client.post(self.url, {'flag': ReviewFlag.SPAM})
        assert response.status_code == 404

    def test_update_flag(self):
        response = self.client.post(self.url, {'flag': ReviewFlag.SPAM})
        assert response.status_code == 200
        assert ReviewFlag.objects.filter(flag=ReviewFlag.SPAM).count() == 1
        assert Review.objects.filter(editorreview=True).count() == 1

        response = self.client.post(self.url, {'flag': ReviewFlag.LANGUAGE})
        assert response.status_code == 200
        assert ReviewFlag.objects.filter(flag=ReviewFlag.LANGUAGE).count() == 1
        assert ReviewFlag.objects.count() == 1
        assert Review.objects.filter(editorreview=True).count() == 1

    def test_flag_with_note(self):
        response = self.client.post(self.url,
                                    {'flag': ReviewFlag.OTHER, 'note': 'xxx'})
        assert response.status_code == 200
        assert ReviewFlag.objects.filter(flag=ReviewFlag.OTHER).count() == (
            1)
        assert ReviewFlag.objects.count() == 1
        assert ReviewFlag.objects.get(flag=ReviewFlag.OTHER).note == 'xxx'
        assert Review.objects.filter(editorreview=True).count() == 1

    def test_bad_flag(self):
        response = self.client.post(self.url, {'flag': 'xxx'})
        assert response.status_code == 400
        assert Review.objects.filter(editorreview=True).count() == 0


class TestDelete(ReviewTest):

    def setUp(self):
        super(TestDelete, self).setUp()
        self.url = helpers.url('addons.reviews.delete',
                               self.addon.slug, 218207)
        self.login_admin()

    def test_no_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        assert response.status_code == 401

    def test_no_perms(self):
        GroupUser.objects.all().delete()
        response = self.client.post(self.url)
        assert response.status_code == 403

    def test_404(self):
        url = helpers.url('addons.reviews.delete', self.addon.slug, 0)
        response = self.client.post(url)
        assert response.status_code == 404

    def test_delete_review_with_dev_reply(self):
        cnt = Review.objects.count()
        response = self.client.post(self.url)
        assert response.status_code == 200
        # Two are gone since we deleted a review with a reply.
        assert Review.objects.count() == cnt - 2

    def test_delete_success(self):
        Review.objects.update(reply_to=None)
        cnt = Review.objects.count()
        response = self.client.post(self.url)
        assert response.status_code == 200
        assert Review.objects.count() == cnt - 1

    def test_delete_own_review(self):
        self.client.logout()
        self.login_dev()
        url = helpers.url('addons.reviews.delete', self.addon.slug, 218468)
        cnt = Review.objects.count()
        response = self.client.post(url)
        assert response.status_code == 200
        assert Review.objects.count() == cnt - 1
        assert not Review.objects.filter(pk=218468).exists()

    def test_reviewer_can_delete(self):
        # Test an editor can delete a review if not listed as an author.
        user = UserProfile.objects.get(email='trev@adblockplus.org')
        # Remove user from authors.
        AddonUser.objects.filter(addon=self.addon).delete()
        # Make user an add-on reviewer.
        group = Group.objects.create(name='Reviewer', rules='Addons:Review')
        GroupUser.objects.create(group=group, user=user)

        self.client.logout()
        self.login_dev()

        cnt = Review.objects.count()
        response = self.client.post(self.url)
        assert response.status_code == 200
        # Two are gone since we deleted a review with a reply.
        assert Review.objects.count() == cnt - 2
        assert not Review.objects.filter(pk=218207).exists()

    def test_editor_own_addon_cannot_delete(self):
        # Test an editor cannot delete a review if listed as an author.
        user = UserProfile.objects.get(email='trev@adblockplus.org')
        # Make user an add-on reviewer.
        group = Group.objects.create(name='Reviewer', rules='Addons:Review')
        GroupUser.objects.create(group=group, user=user)

        self.client.logout()
        self.login_dev()

        cnt = Review.objects.count()
        response = self.client.post(self.url)
        assert response.status_code == 403
        assert Review.objects.count() == cnt
        assert Review.objects.filter(pk=218207).exists()


class TestCreate(ReviewTest):

    def setUp(self):
        super(TestCreate, self).setUp()
        self.add_url = helpers.url('addons.reviews.add', self.addon.slug)
        self.client.login(username='root_x@ukr.net', password='password')
        self.user = UserProfile.objects.get(email='root_x@ukr.net')
        self.qs = Review.objects.filter(addon=1865)
        self.more_url = self.addon.get_url_path(more=True)
        self.list_url = helpers.url('addons.reviews.list', self.addon.slug)

    def test_add_logged(self):
        r = self.client.get(self.add_url)
        assert r.status_code == 200
        self.assertTemplateUsed(r, 'reviews/add.html')

    def test_add_link_visitor(self):
        """
        Ensure non-logged user can see Add Review links on details page
        but not on Reviews listing page.
        """
        self.client.logout()
        r = self.client.get_ajax(self.more_url)
        assert pq(r.content)('#add-review').length == 1
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        doc = pq(r.content)
        assert doc('#add-review').length == 0
        assert doc('#add-first-review').length == 0

    def test_add_link_logged(self):
        """Ensure logged user can see Add Review links."""
        r = self.client.get_ajax(self.more_url)
        assert pq(r.content)('#add-review').length == 1
        r = self.client.get(self.list_url)
        doc = pq(r.content)
        assert doc('#add-review').length == 1
        assert doc('#add-first-review').length == 0

    def test_add_link_dev(self):
        """Ensure developer cannot see Add Review links."""
        self.login_dev()
        r = self.client.get_ajax(self.more_url)
        assert pq(r.content)('#add-review').length == 0
        r = self.client.get(helpers.url('addons.reviews.list',
                                        self.addon.slug))
        doc = pq(r.content)
        assert doc('#add-review').length == 0
        assert doc('#add-first-review').length == 0

    def test_list_none_add_review_link_visitor(self):
        """If no reviews, ensure visitor user cannot see Add Review link."""
        Review.objects.all().delete()
        self.client.logout()
        r = self.client.get(self.list_url)
        doc = pq(r.content)('#reviews')
        assert doc('#add-review').length == 0
        assert doc('#no-add-first-review').length == 0
        assert doc('#add-first-review').length == 1

    def test_list_none_add_review_link_logged(self):
        """If no reviews, ensure logged user can see Add Review link."""
        Review.objects.all().delete()
        r = self.client.get(self.list_url)
        doc = pq(r.content)
        assert doc('#add-review').length == 1
        assert doc('#no-add-first-review').length == 0
        assert doc('#add-first-review').length == 1

    def test_list_none_add_review_link_dev(self):
        """If no reviews, ensure developer can see Add Review link."""
        Review.objects.all().delete()
        self.login_dev()
        r = self.client.get(self.list_url)
        doc = pq(r.content)('#reviews')
        assert doc('#add-review').length == 0
        assert doc('#no-add-first-review').length == 1
        assert doc('#add-first-review').length == 0

    def test_body_has_url(self):
        """ test that both the create and revise reviews segments properly
            note reviews that contain URL like patterns for editorial review
        """
        for body in ['url http://example.com', 'address 127.0.0.1',
                     'url https://example.com/foo/bar', 'host example.org',
                     'quote example%2eorg', 'IDNA www.xn--ie7ccp.xxx']:
            self.client.post(self.add_url, {'body': body, 'rating': 2})
            ff = Review.objects.filter(addon=self.addon)
            rf = ReviewFlag.objects.filter(review=ff[0])
            assert ff[0].flag
            assert ff[0].editorreview
            assert rf[0].note == 'URLs'

    def test_mail_and_new_activity_log_on_post(self):
        assert not ActivityLog.objects.exists()
        self.client.post(self.add_url, {'body': u'sômething', 'rating': 2})
        review = self.addon.reviews.latest('pk')
        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == self.user
        assert activity_log.arguments == [self.addon, review]
        assert activity_log.action == amo.LOG.ADD_REVIEW.id

        assert len(mail.outbox) == 1

    def test_mail_but_no_activity_log_on_reply(self):
        # Hard delete existing reply first, because reply() does
        # a get_or_create(), which would make that reply an edit, and that's
        # covered by the other test below.
        Review.objects.filter(id=218468).delete(hard_delete=True)
        review = self.addon.reviews.get()
        ActivityLog.objects.all().delete()
        reply_url = helpers.url(
            'addons.reviews.reply', self.addon.slug, review.pk)
        self.login_dev()
        self.client.post(reply_url, {'body': u'Reeeeeply! Rëëëplyyy'})
        assert ActivityLog.objects.count() == 0

        assert len(mail.outbox) == 1

    def test_new_activity_log_on_reply_but_no_mail_if_one_already_exists(self):
        review = self.addon.reviews.get()
        existing_reply = Review.objects.get(id=218468)
        assert not ActivityLog.objects.exists()
        reply_url = helpers.url(
            'addons.reviews.reply', self.addon.slug, review.pk)
        self.login_dev()
        self.client.post(reply_url, {'body': u'Reeeeeply! Rëëëplyyy'})
        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == existing_reply.user
        assert activity_log.arguments == [self.addon, existing_reply]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0

    def test_cant_review_unlisted_addon(self):
        """Can't review an unlisted addon."""
        self.addon.update(is_listed=False)
        assert self.client.get(self.add_url).status_code == 404


class TestEdit(ReviewTest):

    def setUp(self):
        super(TestEdit, self).setUp()
        self.client.login(username='root_x@ukr.net', password='password')

    def test_edit(self):
        url = helpers.url('addons.reviews.edit', self.addon.slug, 218207)
        response = self.client.post(url, {'rating': 2, 'body': 'woo woo'},
                                    X_REQUESTED_WITH='XMLHttpRequest')
        assert response.status_code == 200
        assert response['Content-type'] == 'application/json'
        assert '%s' % Review.objects.get(id=218207).body == 'woo woo'

        response = self.client.get(helpers.url('addons.reviews.list',
                                   self.addon.slug))
        doc = pq(response.content)
        assert doc('#review-218207 .review-edit').text() == 'Edit review'

    def test_edit_not_owner(self):
        url = helpers.url('addons.reviews.edit', self.addon.slug, 218468)
        r = self.client.post(url, {'rating': 2, 'body': 'woo woo'},
                             X_REQUESTED_WITH='XMLHttpRequest')
        assert r.status_code == 403

    def test_edit_deleted(self):
        Review.objects.get(pk=218207).delete()
        url = helpers.url('addons.reviews.edit', self.addon.slug, 218207)
        response = self.client.post(url, {'rating': 2, 'body': 'woo woo'},
                                    X_REQUESTED_WITH='XMLHttpRequest')
        assert response.status_code == 404

    def test_edit_reply(self):
        self.login_dev()
        url = helpers.url('addons.reviews.edit', self.addon.slug, 218468)
        response = self.client.post(url, {'title': 'fo', 'body': 'shizzle'},
                                    X_REQUESTED_WITH='XMLHttpRequest')
        assert response.status_code == 200
        reply = Review.objects.get(id=218468)
        assert '%s' % reply.title == 'fo'
        assert '%s' % reply.body == 'shizzle'

        response = self.client.get(helpers.url('addons.reviews.list',
                                   self.addon.slug))
        doc = pq(response.content)
        assert doc('#review-218468 .review-reply-edit').text() == 'Edit reply'

    def test_new_activity_log_but_no_mail_on_edit(self):
        review = Review.objects.get(pk=218207)
        assert not ActivityLog.objects.exists()
        user = review.user
        edit_url = helpers.url(
            'addons.reviews.edit', self.addon.slug, review.pk)
        self.client.post(edit_url, {'body': u'Edîted.', 'rating': 1})
        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == user
        assert activity_log.arguments == [self.addon, review]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0

    def test_new_activity_log_but_no_mail_on_edit_by_admin(self):
        review = Review.objects.get(pk=218207)
        assert not ActivityLog.objects.exists()
        original_user = review.user
        admin_user = UserProfile.objects.get(pk=4043307)
        self.login_admin()
        edit_url = helpers.url(
            'addons.reviews.edit', self.addon.slug, review.pk)
        self.client.post(edit_url, {'body': u'Edîted.', 'rating': 1})
        review.reload()
        assert review.user == original_user
        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == admin_user
        assert activity_log.arguments == [self.addon, review]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0

    def test_new_activity_log_but_no_mail_on_reply_edit(self):
        review = Review.objects.get(pk=218468)
        assert not ActivityLog.objects.exists()
        user = review.user
        edit_url = helpers.url(
            'addons.reviews.edit', self.addon.slug, review.pk)
        self.login_dev()
        self.client.post(edit_url, {'body': u'Reeeeeply! Rëëëplyyy'})
        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == user
        assert activity_log.arguments == [self.addon, review]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0


class TestTranslate(ReviewTest):

    def setUp(self):
        super(TestTranslate, self).setUp()
        self.create_switch('reviews-translate')
        self.user = UserProfile.objects.get(username='jbalogh')
        self.review = Review.objects.create(addon=self.addon, user=self.user,
                                            title='or', body='yes')

    def test_regular_call(self):
        url = helpers.url('addons.reviews.translate', self.review.addon.slug,
                          self.review.id, 'fr')
        r = self.client.get(url)
        assert r.status_code == 302
        assert r.get('Location') == 'https://translate.google.com/#auto/fr/yes'

    def test_translate_deleted(self):
        self.review.delete()
        url = helpers.url('addons.reviews.translate', self.review.addon.slug,
                          self.review.id, 'fr')
        response = self.client.get(url)
        assert response.status_code == 404

    def test_supports_dsb_hsb(self):
        # Make sure 3 character long locale codes resolve properly.
        for code in ('dsb', 'hsb'):
            review = self.review
            url = helpers.url('addons.reviews.translate', review.addon.slug,
                              review.id, code)
            r = self.client.get(url)
            assert r.status_code == 302
            expected = 'https://translate.google.com/#auto/{}/yes'.format(code)
            assert r.get('Location') == expected

    def test_unicode_call(self):
        review = Review.objects.create(addon=self.addon, user=self.user,
                                       title='or', body=u'héhé 3%')
        url = helpers.url('addons.reviews.translate',
                          review.addon.slug, review.id, 'fr')
        r = self.client.get(url)
        assert r.status_code == 302
        assert r.get('Location') == (
            'https://translate.google.com/#auto/fr/h%C3%A9h%C3%A9%203%25')

    @mock.patch('olympia.reviews.views.requests')
    def test_ajax_call(self, requests):
        # Mock requests.
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {u'data': {u'translations': [{
            u'translatedText': u'oui',
            u'detectedSourceLanguage': u'en'
        }]}}
        requests.get.return_value = response

        # Call translation.
        review = self.review
        url = helpers.url('addons.reviews.translate', review.addon.slug,
                          review.id, 'fr')
        r = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        assert r.status_code == 200
        assert json.loads(r.content) == {"body": "oui", "title": "oui"}

    @mock.patch('waffle.switch_is_active', lambda x: True)
    @mock.patch('olympia.reviews.views.requests')
    def test_invalid_api_key(self, requests):
        # Mock requests.
        response = mock.Mock()
        response.status_code = 400
        response.json.return_value = {'error': {'code': 400, 'errors': [{
            'domain': 'usageLimits', 'message': 'Bad Request',
            'reason': 'keyInvalid'}], 'message': 'Bad Request'}}
        requests.get.return_value = response

        # Call translation.
        review = self.review
        url = helpers.url('addons.reviews.translate', review.addon.slug,
                          review.id, 'fr')
        r = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        assert r.status_code == 400


class TestMobileReviews(MobileTest, TestCase):
    fixtures = ['reviews/dev-reply.json', 'base/admin', 'base/users']

    def setUp(self):
        super(TestMobileReviews, self).setUp()
        self.addon = Addon.objects.get(id=1865)
        self.user = UserProfile.objects.get(email='regular@mozilla.com')
        self.login_regular()
        self.add_url = helpers.url('addons.reviews.add', self.addon.slug)
        self.list_url = helpers.url('addons.reviews.list', self.addon.slug)

    def login_regular(self):
        self.client.login(username='regular@mozilla.com', password='password')

    def login_dev(self):
        self.client.login(username='trev@adblockplus.org', password='password')

    def login_admin(self):
        self.client.login(username='jbalogh@mozilla.com', password='password')

    def test_mobile(self):
        self.client.logout()
        self.mobile_init()
        r = self.client.get(self.list_url)
        assert r.status_code == 200
        self.assertTemplateUsed(r, 'reviews/mobile/review_list.html')

    def test_add_visitor(self):
        self.client.logout()
        self.mobile_init()
        r = self.client.get(self.add_url)
        assert r.status_code == 302

    def test_add_logged(self):
        r = self.client.get(self.add_url)
        assert r.status_code == 200
        self.assertTemplateUsed(r, 'reviews/mobile/add.html')

    def test_add_admin(self):
        self.login_admin()
        r = self.client.get(self.add_url)
        assert r.status_code == 200

    def test_add_dev(self):
        self.login_dev()
        r = self.client.get(self.add_url)
        assert r.status_code == 403

    def test_add_link_visitor(self):
        self.client.logout()
        self.mobile_init()
        r = self.client.get(self.list_url)
        doc = pq(r.content)
        assert doc('#add-review').length == 1
        assert doc('.copy .login-button').length == 1
        assert doc('#review-form').length == 0

    def test_add_link_logged(self):
        r = self.client.get(self.list_url)
        doc = pq(r.content)
        assert doc('#add-review').length == 1
        assert doc('#review-form').length == 1

    def test_add_link_dev(self):
        self.login_dev()
        r = self.client.get(self.list_url)
        doc = pq(r.content)
        assert doc('#add-review').length == 0
        assert doc('#review-form').length == 0

    def test_add_submit(self):
        r = self.client.post(self.add_url, {'body': 'hi', 'rating': 3})
        assert r.status_code == 302

        r = self.client.get(self.list_url)
        doc = pq(r.content)
        text = doc('.review').eq(0).text()
        assert "hi" in text
        assert "Rated 3 out of 5" in text

    def test_add_logged_out(self):
        self.client.logout()
        self.mobile_init()
        r = self.client.get(helpers.url('addons.reviews.add', self.addon.slug))
        assert r.status_code == 302


class TestReviewViewSetGet(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.pk})

    def test_list(self, **kwargs):
        review1 = Review.objects.create(
            addon=self.addon, body='review 1', user=user_factory(),
            rating=1)
        review2 = Review.objects.create(
            addon=self.addon, body='review 2', user=user_factory(),
            rating=2)
        review1.update(created=self.days_ago(1))
        # Add a review belonging to a different add-on, a reply, a deleted
        # review and another older review by the same user as the first review.
        # They should not be present in the list.
        review_deleted = Review.objects.create(
            addon=self.addon, body='review deleted', user=review1.user,
            rating=3)
        review_deleted.delete()
        Review.objects.create(
            addon=self.addon, body='reply to review 1', reply_to=review1,
            user=user_factory())
        Review.objects.create(
            addon=addon_factory(), body='review other addon',
            user=user_factory(), rating=4)
        older_review = Review.objects.create(
            addon=self.addon, body='review same user/addon older',
            user=review1.user, rating=5)
        # We change `created` manually after the actual creation, so we need to
        # force a full refresh of the denormalized fields, because this
        # normally only happens at creation time.
        older_review.update(created=self.days_ago(42))
        older_review.update_denormalized_fields()
        assert review1.reload().is_latest is True
        assert older_review.reload().is_latest is False

        assert Review.unfiltered.count() == 6

        response = self.client.get(self.url, kwargs)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['count'] == 2
        assert data['results']
        assert len(data['results']) == 2
        assert data['results'][0]['id'] == review2.pk
        assert data['results'][1]['id'] == review1.pk
        return data

    def test_list_grouped_ratings(self):
        data = self.test_list(show_grouped_ratings=1)
        assert data['grouped_ratings']['1'] == 1
        assert data['grouped_ratings']['2'] == 1
        assert data['grouped_ratings']['3'] == 0
        assert data['grouped_ratings']['4'] == 0
        assert data['grouped_ratings']['5'] == 0

    def test_list_unknown_addon(self, **kwargs):
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.pk + 42})
        response = self.client.get(self.url, kwargs)
        assert response.status_code == 404
        data = json.loads(response.content)
        return data

    def test_list_grouped_ratings_unknown_addon_not_present(self):
        data = self.test_list_unknown_addon(show_grouped_ratings=1)
        assert 'grouped_ratings' not in data

    def test_list_addon_guid(self):
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.guid})
        self.test_list()

    def test_list_addon_slug(self):
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.slug})
        self.test_list()

    def test_list_user(self, **kwargs):
        self.user = user_factory()
        self.url = reverse(
            'account-review-list', kwargs={'account_pk': self.user.pk})
        review1 = Review.objects.create(
            addon=self.addon, body='review 1', user=self.user)
        review2 = Review.objects.create(
            addon=self.addon, body='review 2', user=self.user)
        review1.update(created=self.days_ago(1))
        review2.update(created=self.days_ago(2))
        # Add a review belonging to a different user, a reply and a deleted
        # review. The reply should show up since it's made by the right user,
        # but the rest should be ignored.
        review_deleted = Review.objects.create(
            addon=self.addon, body='review deleted', user=self.user)
        review_deleted.delete()
        other_review = Review.objects.create(
            addon=addon_factory(), body='review from other user',
            user=user_factory())
        reply = Review.objects.create(
            addon=other_review.addon, body='reply to other user',
            reply_to=other_review, user=self.user)

        assert Review.unfiltered.count() == 5

        response = self.client.get(self.url, kwargs)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['count'] == 3
        assert data['results']
        assert len(data['results']) == 3
        assert data['results'][0]['id'] == reply.pk
        assert data['results'][1]['id'] == review1.pk
        assert data['results'][2]['id'] == review2.pk
        return data

    def test_list_user_grouped_ratings_not_present(self):
        data = self.test_list_user(show_grouped_ratings=1)
        assert 'grouped_ratings' not in data

    def test_list_no_user_or_addon(self):
        # We have a fallback in get_queryset() to avoid listing all reviews on
        # the website if somehow we messed up the if conditions. It should not
        # be possible to reach it, but test it by forcing the instantiation of
        # the viewset with no kwargs other than action='list'.
        view = ReviewViewSet(action='list', kwargs={},
                             request=APIRequestFactory().get('/'))
        with self.assertRaises(ParseError):
            view.filter_queryset(view.get_queryset())

    def test_detail(self):
        review = Review.objects.create(
            addon=self.addon, body='review 1', user=user_factory())
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': review.pk})

        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['id'] == review.pk

    def test_detail_reply(self):
        review = Review.objects.create(
            addon=self.addon, body='review', user=user_factory())
        reply = Review.objects.create(
            addon=self.addon, body='reply to review', user=user_factory(),
            reply_to=review)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': reply.pk})

        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['id'] == reply.pk

    def test_detail_deleted(self):
        review = Review.objects.create(
            addon=self.addon, body='review 1', user=user_factory())
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': review.pk})
        review.delete()

        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_detail_deleted_reply(self):
        review = Review.objects.create(
            addon=self.addon, body='review', user=user_factory())
        reply = Review.objects.create(
            addon=self.addon, body='reply to review', user=user_factory(),
            reply_to=review)
        reply.delete()
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': review.pk})

        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['id'] == review.pk
        assert data['reply'] is None

    def test_detail_show_deleted_admin(self):
        self.user = user_factory()
        self.grant_permission(self.user, 'Addons:Edit')
        self.client.login_api(self.user)
        review = Review.objects.create(
            addon=self.addon, body='review', user=user_factory())
        reply = Review.objects.create(
            addon=self.addon, body='reply to review', user=user_factory(),
            reply_to=review)
        reply.delete()
        review.delete()
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': review.pk})

        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['id'] == review.pk
        assert data['reply']
        assert data['reply']['id'] == reply.pk

    def test_list_by_admin_does_not_show_deleted_by_default(self):
        self.user = user_factory()
        self.grant_permission(self.user, 'Addons:Edit')
        self.client.login_api(self.user)
        review1 = Review.objects.create(
            addon=self.addon, body='review 1', user=user_factory())
        review2 = Review.objects.create(
            addon=self.addon, body='review 2', user=user_factory())
        review1.update(created=self.days_ago(1))
        # Add a review belonging to a different add-on, a reply and a deleted
        # review. They should not be present in the list.
        review_deleted = Review.objects.create(
            addon=self.addon, body='review deleted', user=review1.user)
        review_deleted.delete()
        Review.objects.create(
            addon=self.addon, body='reply to review 2', reply_to=review2,
            user=user_factory())
        Review.objects.create(
            addon=addon_factory(), body='review other addon',
            user=review1.user)
        # Also add a deleted reply to the first review, it should not be shown.
        deleted_reply = Review.objects.create(
            addon=self.addon, body='reply to review 1', reply_to=review1,
            user=user_factory())
        deleted_reply.delete()

        assert Review.unfiltered.count() == 6

        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['count'] == 2
        assert data['results']
        assert len(data['results']) == 2
        assert data['results'][0]['id'] == review2.pk
        assert data['results'][0]['reply'] is not None
        assert data['results'][1]['id'] == review1.pk
        assert data['results'][1]['reply'] is None

    def test_list_admin_show_deleted_if_requested(self):
        self.user = user_factory()
        self.grant_permission(self.user, 'Addons:Edit')
        self.client.login_api(self.user)
        review1 = Review.objects.create(
            addon=self.addon, body='review 1', user=user_factory())
        review2 = Review.objects.create(
            addon=self.addon, body='review 2', user=user_factory())
        review1.update(created=self.days_ago(1))
        # Add a review belonging to a different add-on, a reply and a deleted
        # review. The deleted review should be present, not the rest.
        review_deleted = Review.objects.create(
            addon=self.addon, body='review deleted', user=review1.user)
        review_deleted.update(created=self.days_ago(2))
        review_deleted.delete()
        Review.objects.create(
            addon=self.addon, body='reply to review 2', reply_to=review2,
            user=user_factory())
        Review.objects.create(
            addon=addon_factory(), body='review other addon',
            user=review1.user)
        # Also add a deleted reply to the first review, it should be shown
        # as a child of that review.
        deleted_reply = Review.objects.create(
            addon=self.addon, body='reply to review 1', reply_to=review1,
            user=user_factory())
        deleted_reply.delete()

        assert Review.unfiltered.count() == 6

        response = self.client.get(self.url, data={'filter': 'with_deleted'})
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['count'] == 3
        assert data['results']
        assert len(data['results']) == 3
        assert data['results'][0]['id'] == review2.pk
        assert data['results'][0]['reply'] is not None
        assert data['results'][1]['id'] == review1.pk
        assert data['results'][1]['reply'] is not None
        assert data['results'][1]['reply']['id'] == deleted_reply.pk
        assert data['results'][2]['id'] == review_deleted.pk


class TestReviewViewSetDelete(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.user = user_factory()
        self.review = Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.user)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk})

    def test_delete_anonymous(self):
        response = self.client.delete(self.url)
        assert response.status_code == 401

    def test_delete_no_rights(self):
        other_user = user_factory()
        self.client.login_api(other_user)
        response = self.client.delete(self.url)
        assert response.status_code == 403

    def test_delete_admin(self):
        admin_user = user_factory()
        self.grant_permission(admin_user, 'Addons:Edit')
        self.client.login_api(admin_user)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert Review.objects.count() == 0
        assert Review.unfiltered.count() == 1

    def test_delete_editor(self):
        admin_user = user_factory()
        self.grant_permission(admin_user, 'Addons:Review')
        self.client.login_api(admin_user)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert Review.objects.count() == 0
        assert Review.unfiltered.count() == 1

    def test_delete_editor_but_addon_author(self):
        admin_user = user_factory()
        self.addon.addonuser_set.create(user=admin_user)
        self.grant_permission(admin_user, 'Addons:Review')
        self.client.login_api(admin_user)
        response = self.client.delete(self.url)
        assert response.status_code == 403
        assert Review.objects.count() == 1

    def test_delete_owner(self):
        self.client.login_api(self.user)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert Review.objects.count() == 0
        assert Review.unfiltered.count() == 1

    def test_delete_owner_reply(self):
        addon_author = user_factory()
        self.addon.addonuser_set.create(user=addon_author)
        self.client.login_api(addon_author)
        reply = Review.objects.create(
            addon=self.addon, reply_to=self.review,
            body=u'Reply that will be delêted...', user=addon_author)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': reply.pk})

        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert Review.objects.count() == 1
        assert Review.unfiltered.count() == 2

    def test_delete_404(self):
        self.client.login_api(self.user)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk + 42})
        response = self.client.delete(self.url)
        assert response.status_code == 404
        assert Review.objects.count() == 1


class TestReviewViewSetEdit(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.user = user_factory(username='areviewuser')
        self.review = Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body=u'My revïew', title=u'Titlé', user=self.user)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk})

    def test_edit_anonymous(self):
        response = self.client.patch(self.url, {'body': u'løl!'})
        assert response.status_code == 401

        response = self.client.put(self.url, {'body': u'løl!'})
        assert response.status_code == 405

    def test_edit_no_rights(self):
        other_user = user_factory()
        self.client.login_api(other_user)
        response = self.client.patch(self.url, {'body': u'løl!'})
        assert response.status_code == 403

        response = self.client.put(self.url, {'body': u'løl!'})
        assert response.status_code == 405

    def test_edit_no_rights_even_editor(self):
        # Only admins can edit a review they didn't write themselves.
        editor_user = user_factory()
        self.grant_permission(editor_user, 'Addons:Review')
        self.client.login_api(editor_user)
        response = self.client.patch(self.url, {'body': u'løl!'})
        assert response.status_code == 403

        response = self.client.put(self.url, {'body': u'løl!'})
        assert response.status_code == 405

    def test_edit_owner_partial(self):
        original_created_date = self.days_ago(1)
        self.review.update(created=original_created_date)
        self.client.login_api(self.user)
        response = self.client.patch(self.url, {'rating': 2, 'body': u'løl!'})
        assert response.status_code == 200
        self.review.reload()
        assert response.data['id'] == self.review.pk
        assert response.data['body'] == unicode(self.review.body) == u'løl!'
        assert response.data['title'] == unicode(self.review.title) == u'Titlé'
        assert response.data['rating'] == self.review.rating == 2
        assert response.data['version'] == self.review.version.version
        assert self.review.created == original_created_date

        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == self.user
        assert activity_log.arguments == [self.addon, self.review]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0

    def test_edit_owner_put_not_allowed(self):
        self.client.login_api(self.user)
        response = self.client.put(self.url, {'body': u'løl!'})
        assert response.status_code == 405

    def test_edit_dont_allow_version_to_be_edited(self):
        self.client.login_api(self.user)
        new_version = version_factory(addon=self.addon)
        response = self.client.patch(self.url, {'version': new_version.pk})
        assert response.status_code == 400
        assert response.data['version'] == [
            'Can not change version once the review has been created.']

    def test_edit_admin(self):
        original_review_user = self.review.user
        admin_user = user_factory(username='mylittleadmin')
        self.grant_permission(admin_user, 'Addons:Edit')
        self.client.login_api(admin_user)
        response = self.client.patch(self.url, {'body': u'løl!'})
        assert response.status_code == 200
        self.review.reload()
        assert response.data['id'] == self.review.pk
        assert response.data['body'] == unicode(self.review.body) == u'løl!'
        assert response.data['title'] == unicode(self.review.title) == u'Titlé'
        assert response.data['version'] == self.review.version.version
        assert self.review.user == original_review_user

        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == admin_user
        assert activity_log.arguments == [self.addon, self.review]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0

    def test_edit_reply(self):
        addon_author = user_factory()
        self.addon.addonuser_set.create(user=addon_author)
        self.client.login_api(addon_author)
        reply = Review.objects.create(
            reply_to=self.review, body=u'This is â reply', user=addon_author,
            addon=self.addon)
        self.url = reverse(
            'addon-review-detail',
            kwargs={'addon_pk': self.addon.pk, 'pk': reply.pk})

        response = self.client.patch(self.url, {'rating': 5})
        assert response.status_code == 200
        # Since the review we're editing was a reply, rating' was an unknown
        # parameter and was ignored.
        reply.reload()
        assert reply.rating is None
        assert 'rating' not in response.data

        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == addon_author
        assert activity_log.arguments == [self.addon, reply]
        assert activity_log.action == amo.LOG.EDIT_REVIEW.id

        assert len(mail.outbox) == 0


class TestReviewViewSetPost(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.pk})

    def test_post_anonymous(self):
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5})
        assert response.status_code == 401

    def test_post_no_version(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5})
        assert response.status_code == 400
        assert response.data['version'] == [u'This field is required.']

    def test_post_version_string(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': self.addon.current_version.version})
        assert response.status_code == 400
        assert response.data['version'] == [
            'Incorrect type. Expected pk value, received unicode.']

    def test_post_logged_in(self):
        addon_author = user_factory()
        self.addon.addonuser_set.create(user=addon_author)
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': u'blahé', 'rating': 5,
            'version': self.addon.current_version.pk},
            REMOTE_ADDR='213.225.312.5')
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert unicode(review.body) == response.data['body'] == u'test bodyé'
        assert review.rating == response.data['rating'] == 5
        assert review.user == self.user
        assert unicode(review.title) == response.data['title'] == u'blahé'
        assert review.reply_to is None
        assert review.addon == self.addon
        assert review.version == self.addon.current_version
        assert response.data['version'] == review.version.version
        assert 'ip_address' not in response.data
        assert review.ip_address == '213.225.312.5'
        assert not review.flag
        assert not review.editorreview

        activity_log = ActivityLog.objects.latest('pk')
        assert activity_log.user == self.user
        assert activity_log.arguments == [self.addon, review]
        assert activity_log.action == amo.LOG.ADD_REVIEW.id

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [addon_author.email]

    def test_post_auto_flagged_and_cleaned(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        body = u'Trying to spam <br> http://éxample.com'
        cleaned_body = u'Trying to spam \n http://éxample.com'
        response = self.client.post(self.url, {
            'body': body, 'title': u'blahé', 'rating': 5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert unicode(review.body) == response.data['body'] == cleaned_body
        assert review.rating == response.data['rating'] == 5
        assert review.user == self.user
        assert unicode(review.title) == response.data['title'] == u'blahé'
        assert review.reply_to is None
        assert review.addon == self.addon
        assert review.version == self.addon.current_version
        assert response.data['version'] == review.version.version
        assert review.flag
        assert review.editorreview

    def test_post_rating_float(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 4.5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['rating'] == ['A valid integer is required.']

    def test_post_rating_too_big(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 6,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['rating'] == [
            'Ensure this value is less than or equal to 5.']

    def test_post_rating_too_low(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 0,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['rating'] == [
            'Ensure this value is greater than or equal to 1.']

    def test_post_rating_no_title(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert unicode(review.body) == response.data['body'] == u'test bodyé'
        assert review.rating == response.data['rating'] == 5
        assert review.user == self.user
        assert review.title is None
        assert response.data['title'] is None
        assert review.reply_to is None
        assert review.addon == self.addon
        assert review.version == self.addon.current_version
        assert response.data['version'] == review.version.version

    def test_no_body_or_title_just_rating(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': None, 'title': None, 'rating': 5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert review.body is None
        assert response.data['body'] is None
        assert review.rating == response.data['rating'] == 5
        assert review.user == self.user
        assert review.title is None
        assert response.data['title'] is None
        assert review.reply_to is None
        assert review.addon == self.addon
        assert review.version == self.addon.current_version
        assert response.data['version'] == review.version.version

    def test_omit_body_and_title_completely_just_rating(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'rating': 5, 'version': self.addon.current_version.pk})
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert review.body is None
        assert response.data['body'] is None
        assert review.rating == response.data['rating'] == 5
        assert review.user == self.user
        assert review.title is None
        assert response.data['title'] is None
        assert review.reply_to is None
        assert review.addon == self.addon
        assert review.version == self.addon.current_version
        assert response.data['version'] == review.version.version

    def test_post_rating_rating_required(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['rating'] == ['This field is required.']

    def test_post_no_such_addon_id(self):
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.pk + 42})
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(self.url, {
            'body': 'test body', 'title': None, 'rating': 5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 404

    def test_post_version_not_linked_to_the_right_addon(self):
        addon2 = addon_factory()
        self.url = reverse(
            'addon-review-list', kwargs={'addon_pk': self.addon.pk})
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(self.url, {
            'body': 'test body', 'title': None, 'rating': 5,
            'version': addon2.current_version.pk})
        assert response.status_code == 400
        assert response.data['version'] == [
            'Version does not exist on this add-on or is not public.']

    def test_post_deleted_addon(self):
        version_pk = self.addon.current_version.pk
        self.addon.delete()
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': version_pk})
        assert response.status_code == 404

    def test_post_deleted_version(self):
        old_version_pk = self.addon.current_version.pk
        old_version = self.addon.current_version
        new_version = version_factory(addon=self.addon)
        old_version.delete()
        # Just in case, make sure the add-on is still public.
        self.addon.reload()
        assert self.addon.current_version == new_version
        assert self.addon.status

        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': old_version_pk})
        assert response.status_code == 400
        assert response.data['version'] == [
            'Version does not exist on this add-on or is not public.']

    def test_post_disabled_version(self):
        self.addon.current_version.update(created=self.days_ago(1))
        new_version = version_factory(addon=self.addon)
        old_version = self.addon.current_version
        old_version.files.update(status=amo.STATUS_DISABLED)
        assert self.addon.current_version == new_version
        assert self.addon.status == amo.STATUS_PUBLIC

        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': old_version.pk})
        assert response.status_code == 400
        assert response.data['version'] == [
            'Version does not exist on this add-on or is not public.']

    def test_post_not_public_addon(self):
        version_pk = self.addon.current_version.pk
        self.addon.update(status=amo.STATUS_NULL)
        self.user = user_factory()
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': version_pk})
        assert response.status_code == 403

    def test_post_logged_in_but_is_addon_author(self):
        self.user = user_factory()
        self.addon.addonuser_set.create(user=self.user)
        self.client.login_api(self.user)
        assert not Review.objects.exists()
        response = self.client.post(self.url, {
            'body': u'test bodyé', 'title': None, 'rating': 5,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['non_field_errors'] == [
            'An add-on author can not leave a review on its own add-on.']

    def test_post_twice_different_version(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.user)
        second_version = version_factory(addon=self.addon)
        response = self.client.post(self.url, {
            'body': u'My ôther review', 'title': None, 'rating': 2,
            'version': second_version.pk})
        assert response.status_code == 201
        assert Review.objects.count() == 2

    def test_post_twice_same_version(self):
        # Posting a review more than once for the same version is not allowed.
        self.user = user_factory()
        self.client.login_api(self.user)
        Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.user)
        response = self.client.post(self.url, {
            'body': u'My ôther review', 'title': None, 'rating': 2,
            'version': self.addon.current_version.pk})
        assert response.status_code == 400
        assert response.data['non_field_errors'] == [
            'The same user can not leave a review on the same version more'
            ' than once.']


class TestReviewViewSetFlag(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.review_user = user_factory()
        self.review = Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.review_user)
        self.url = reverse(
            'addon-review-flag',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk})

    def test_flag_anonymous(self):
        response = self.client.post(self.url)
        assert response.status_code == 401
        assert self.review.reload().editorreview is False

    def test_flag_logged_in_no_flag_field(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(self.url)
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data['flag'] == [u'This field is required.']
        assert self.review.reload().editorreview is False

    def test_flag_logged_in(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam'})
        assert response.status_code == 202
        assert ReviewFlag.objects.count() == 1
        flag = ReviewFlag.objects.latest('pk')
        assert flag.flag == 'review_flag_reason_spam'
        assert flag.user == self.user
        assert flag.review == self.review
        assert self.review.reload().editorreview is True

    def test_flag_logged_in_with_note(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam',
                            'note': u'This is my nøte.'})
        assert response.status_code == 202
        assert ReviewFlag.objects.count() == 1
        flag = ReviewFlag.objects.latest('pk')
        # Flag was changed automatically since a note is being posted.
        assert flag.flag == 'review_flag_reason_other'
        assert flag.user == self.user
        assert flag.review == self.review
        assert flag.note == u'This is my nøte.'
        assert self.review.reload().editorreview is True

    def test_flag_reason_other_without_notes_is_forbidden(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_other'})
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data['__all__'] == [
            'A short explanation must be provided when selecting "Other".']

    def test_flag_logged_in_unknown_flag_type(self):
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'lol'})
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data['flag'] == [
            'Select a valid choice. lol is not one of the available choices.']
        assert self.review.reload().editorreview is False

    def test_flag_logged_in_flag_already_exists(self):
        other_user = user_factory()
        other_flag = ReviewFlag.objects.create(
            user=other_user, review=self.review,
            flag='review_flag_reason_language')
        self.user = user_factory()
        flag = ReviewFlag.objects.create(
            user=self.user, review=self.review,
            flag='review_flag_reason_other')
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam'})
        assert response.status_code == 202
        # We should have re-used the existing flag posted by self.user, so the
        # count should still be 2.
        assert ReviewFlag.objects.count() == 2
        flag.reload()
        # Flag was changed from other to spam.
        assert flag.flag == 'review_flag_reason_spam'
        assert flag.user == self.user
        assert flag.review == self.review
        # Other flag was untouched.
        other_flag.reload()
        assert other_flag.user == other_user
        assert other_flag.flag == 'review_flag_reason_language'
        assert other_flag.review == self.review
        assert self.review.reload().editorreview is True

    def test_flag_logged_in_addon_denied(self):
        self.addon.update(is_listed=False)
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam'})
        assert response.status_code == 403
        assert self.review.reload().editorreview is False

    def test_flag_logged_in_no_such_review(self):
        self.review.delete()
        self.user = user_factory()
        self.client.login_api(self.user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam'})
        assert response.status_code == 404
        assert Review.unfiltered.get(pk=self.review.pk).editorreview is False

    def test_flag_logged_in_review_author(self):
        self.client.login_api(self.review_user)
        response = self.client.post(
            self.url, data={'flag': 'review_flag_reason_spam'})
        assert response.status_code == 403
        assert self.review.reload().editorreview is False


class TestReviewViewSetReply(TestCase):
    client_class = APITestClient

    def setUp(self):
        self.addon = addon_factory(
            guid=generate_addon_guid(), name=u'My Addôn', slug='my-addon')
        self.review_user = user_factory()
        self.review = Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.review_user)
        self.url = reverse(
            'addon-review-reply',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk})

    def test_url(self):
        expected_url = '/api/v3/addons/addon/%d/reviews/%d/reply/' % (
            self.addon.pk, self.review.pk)
        assert self.url == expected_url

    def test_get_method_not_allowed(self):
        self.addon_author = user_factory()
        self.addon.addonuser_set.create(user=self.addon_author)
        self.client.login_api(self.addon_author)
        response = self.client.get(self.url)
        assert response.status_code == 405

    def test_reply_anonymous(self):
        response = self.client.post(self.url, data={})
        assert response.status_code == 401

    def test_reply_non_addon_author(self):
        self.client.login_api(self.review_user)
        response = self.client.post(self.url, data={})
        assert response.status_code == 403

    def test_reply_no_such_review(self):
        self.addon_author = user_factory()
        self.addon.addonuser_set.create(user=self.addon_author)
        self.client.login_api(self.addon_author)
        self.url = reverse(
            'addon-review-reply',
            kwargs={'addon_pk': self.addon.pk, 'pk': self.review.pk + 42})
        response = self.client.post(self.url, data={})
        assert response.status_code == 404

    def test_reply_admin(self):
        self.admin_user = user_factory()
        self.grant_permission(self.admin_user, 'Addons:Edit')
        self.client.login_api(self.admin_user)
        response = self.client.post(self.url, data={
            'body': u'My âdmin réply...',
        })
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert review.body == response.data['body'] == u'My âdmin réply...'
        assert review.rating is None
        assert 'rating' not in response.data
        assert review.user == self.admin_user
        assert review.title is None
        assert response.data['title'] is None
        assert review.reply_to == self.review
        assert 'reply_to' not in response.data  # It's already in the URL...
        assert review.addon == self.addon
        assert review.version is None
        assert 'version' not in response.data

        assert not ActivityLog.objects.exists()

        assert len(mail.outbox) == 1

    def test_reply(self):
        self.addon_author = user_factory()
        self.addon.addonuser_set.create(user=self.addon_author)
        self.client.login_api(self.addon_author)
        response = self.client.post(self.url, data={
            'body': u'My réply...',
        })
        assert response.status_code == 201
        review = Review.objects.latest('pk')
        assert review.pk == response.data['id']
        assert review.body == response.data['body'] == u'My réply...'
        assert review.rating is None
        assert 'rating' not in response.data
        assert review.user == self.addon_author
        assert review.title is None
        assert response.data['title'] is None
        assert review.reply_to == self.review
        assert 'reply_to' not in response.data  # It's already in the URL...
        assert review.addon == self.addon
        assert review.version is None
        assert 'version' not in response.data

        assert not ActivityLog.objects.exists()

        assert len(mail.outbox) == 1

    def test_reply_disabled_addon(self):
        self.addon_author = user_factory()
        self.addon.addonuser_set.create(user=self.addon_author)
        self.client.login_api(self.addon_author)
        self.addon.update(disabled_by_user=True)
        response = self.client.post(self.url, data={})
        assert response.status_code == 403

    def test_replying_to_a_reply_is_not_possible(self):
        self.addon_author = user_factory()
        self.addon.addonuser_set.create(user=self.addon_author)
        self.client.login_api(self.addon_author)
        self.original_review = Review.objects.create(
            addon=self.addon, version=self.addon.current_version, rating=1,
            body='My review', user=self.review_user)
        self.review.update(
            user=self.addon_author, rating=None, reply_to=self.original_review)
        response = self.client.post(self.url, data={
            'body': u'LOL øø!'
        })
        assert response.status_code == 400
        assert response.data['non_field_errors'] == [
            'Can not reply to a review that is already a reply.']
