from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.contrib.auth import signals as auth_signals
from django.contrib.auth.forms import AuthenticationForm
from django.test import TransactionTestCase
from django.test.utils import override_settings

from djactasauth.backends import \
    FilteredModelBackend, ActAsModelBackend, OnlySuperuserCanActAsModelBackend


def create_user(
        username, password='password', is_superuser=False, is_staff=False):
    user = User(username=username, is_superuser=is_superuser)
    user.set_password(password)
    user.save()
    return user


class FilteredBackendTestCase(TransactionTestCase):

    def test_it_is_a_model_backend(self):
        self.assertTrue(
            issubclass(FilteredModelBackend, ModelBackend),
            FilteredModelBackend.__mro__)

    def test_can_declare_filters_which_apply_to_get_user(self):
        staff = create_user(
            username='staff', is_staff=True, is_superuser=False)
        superuser = create_user(
            username='superuser', is_staff=True, is_superuser=True)
        customer = create_user(
            username='customer', is_staff=False, is_superuser=False)
        for u in [staff, superuser, customer]:
            u.set_password('password')
            u.save()

        class TestFilteredBackend(FilteredModelBackend):

            def __init__(self, filter_kwargs):
                self.filter_kwargs = filter_kwargs

        def run_scenarios_with(test_method):
            self.assertEqual(staff, test_method(staff, dict()))
            self.assertEqual(superuser, test_method(superuser, dict()))
            self.assertEqual(customer, test_method(customer, dict()))

            self.assertEqual(None, test_method(customer, dict(is_staff=True)))
            self.assertEqual(
                superuser, test_method(superuser, dict(is_superuser=True)))
            self.assertEqual(
                customer, test_method(
                    customer, dict(username__startswith='c')))
            self.assertEqual(
                None, test_method(superuser, dict(username__startswith='c')))

        def get_user(user, filter_kwargs):
            backend = TestFilteredBackend(filter_kwargs)
            return backend.get_user(user.pk)

        run_scenarios_with(get_user)

        def authenticate(user, filter_kwargs):
            backend = TestFilteredBackend(filter_kwargs)
            return backend.authenticate(
                username=user.username, password='password')

        run_scenarios_with(authenticate)


class ActAsModelBackendTestCase(TransactionTestCase):

    def test_it_is_a_filtered_model_backend(self):
        self.assertTrue(
            issubclass(ActAsModelBackend, FilteredModelBackend),
            ActAsModelBackend.__mro__)

    def test_can_authenticate_user(self):
        user = create_user(username='user', password='password')
        self.assertEqual(
            user, self.authenticate(username='user', password='password'))

    def test_can_become_another_user_with_own_password(self):
        create_user(username='admin', password='admin password')
        user = create_user(username='user', password='user password')
        self.assertEqual(
            None, self.authenticate(
                username='admin/user', password='user password'))
        self.assertEqual(
            user, self.authenticate(
                username='admin/user', password='admin password'))

    def test_cannot_become_nonexistent_user(self):
        create_user(username='admin', password='password')
        self.assertEqual(
            None, self.authenticate(
                username='admin/user', password='password'))

    def test_authenticate_does_not_fire_login_signal(self):
        def should_not_fire_login_signal(user, **kwargs):
            self.fail(
                'should not have fired login signal but did for %r' % user)

        create_user(username='admin', password='admin password')
        user = create_user(username='user', password='user password')

        auth_signals.user_logged_in.connect(should_not_fire_login_signal)
        try:
            self.authenticate(username='admin/user', password='admin password')
        finally:
            auth_signals.user_logged_in.disconnect(
                should_not_fire_login_signal)
        self.assertEqual(
            user, self.authenticate(
                username='admin/user', password='admin password'))

    def test_only_super_user_can_act_as_model_backend_regression(self):
        create_user(
            username='admin1', password='admin1 password', is_superuser=True)
        create_user(
            username='admin2', password='admin2 password', is_superuser=True)
        user = create_user(
            username='user', password='user password', is_superuser=False)

        self.assertEqual(
            None, self.authenticate(
                username='user/admin1', password='user password',
                backend_cls=OnlySuperuserCanActAsModelBackend))
        self.assertEqual(
            None, self.authenticate(
                username='user/admin2', password='user password',
                backend_cls=OnlySuperuserCanActAsModelBackend))

        self.assertEqual(
            user, self.authenticate(
                backend_cls=OnlySuperuserCanActAsModelBackend,
                username='admin1/user', password='admin1 password'))
        self.assertEqual(
            user, self.authenticate(
                backend_cls=OnlySuperuserCanActAsModelBackend,
                username='admin2/user', password='admin2 password'))

        self.assertEqual(
            None, self.authenticate(
                backend_cls=OnlySuperuserCanActAsModelBackend,
                username='admin1/admin2', password='admin1 password'))
        self.assertEqual(
            None, self.authenticate(
                backend_cls=OnlySuperuserCanActAsModelBackend,
                username='admin2/admin1', password='admin2 password'))

    def test_can_customize_can_act_as_policy_by_subclassing(self):
        alice = create_user(username='alice', password='alice')
        create_user(username='bob', password='bob')

        class OnlyShortUserNamesCanActAs(ActAsModelBackend):

            def can_act_as(self, auth_user, user):
                return len(auth_user.username) <= 3

        self.assertEqual(
            None, self.authenticate(
                backend_cls=OnlyShortUserNamesCanActAs,
                username='alice/bob', password='alice'))
        self.assertEqual(
            alice, self.authenticate(
                backend_cls=OnlyShortUserNamesCanActAs,
                username='bob/alice', password='bob'))

###

    def authenticate(self, username, password, backend_cls=None):
        if not backend_cls:
            class EveryoneCanActAs(ActAsModelBackend):
                def can_act_as(self, auth_user, user):
                    return True
            backend_cls = EveryoneCanActAs

        return backend_cls().authenticate(username=username, password=password)


@override_settings(
    AUTHENTICATION_BACKENDS=[
        'djactasauth.backends.OnlySuperuserCanActAsModelBackend'])
class EndToEndActAsThroughFormAndView(TransactionTestCase):

    def test_login_page_is_set_up_as_expected(self):
        response = self.client.get('/login/')
        self.assertEquals(200, response.status_code)
        form = response.context['form']
        self.assertEquals(AuthenticationForm, type(form))

    def test_successful_act_as_login_fires_signal_with_act_as_user(self):
        logged_in_users = []

        def handle_user_logged_in(user, **kwargs):
            logged_in_users.append(user)

        auth_signals.user_logged_in.connect(handle_user_logged_in)
        create_user(username='admin', password='admin', is_superuser=True)
        user = create_user(
            username='user', password='user', is_superuser=False)
        try:
            response = self.client.post(
                '/login/', dict(username='admin/user', password='admin'))
            self.assertEquals(302, response.status_code)
        finally:
            auth_signals.user_logged_in.disconnect(handle_user_logged_in)
        self.assertEqual([user], logged_in_users)

    def test_after_login_correct_user_is_passed_in_the_request_no_act_as(self):
        create_user(username='admin', password='admin', is_superuser=True)
        self.assert_logged_in_user_on_next_request(
            username='admin', password='admin', display_user='admin')

    def test_after_login_correct_user_is_passed_in_the_request_act_as(self):
        create_user(username='admin', password='admin', is_superuser=True)
        create_user(username='user', password='user', is_superuser=False)
        self.assert_logged_in_user_on_next_request(
            username='admin/user', password='admin', display_user='user')

###

    def assert_logged_in_user_on_next_request(
            self, username, password, display_user):
        login_response = self.client.post(
            '/login/', dict(username=username, password=password))
        self.assertEquals(302, login_response.status_code)
        whoami_response = self.client.get('/whoami/')
        self.assertEquals(
            display_user, whoami_response.content.decode('ascii'))
