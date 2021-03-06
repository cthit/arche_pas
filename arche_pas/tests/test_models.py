import unittest

from BTrees.OOBTree import OOBTree
from arche.interfaces import IObjectUpdatedEvent
from arche.interfaces import IWillLoginEvent
from arche.interfaces import IUser
from arche.testing import barebone_fixture
from pyramid import testing
from zope.interface.verify import verifyObject
from zope.interface.verify import verifyClass
from arche.api import User
from pyramid.request import apply_request_extensions
from pyramid.request import Request

from arche_pas.interfaces import IProviderData
from arche_pas.interfaces import IPASProvider
from arche_pas.exceptions import ProviderConfigError


class ProviderDataTests(unittest.TestCase):
    def setUp(self):
        self.config = testing.setUp()

    def tearDown(self):
        testing.tearDown()

    @property
    def _cut(self):
        from arche_pas.models import ProviderData
        return ProviderData

    def test_verify_object(self):
        context = User()
        self.failUnless(verifyObject(IProviderData, self._cut(context)))

    def test_verify_class(self):
        self.failUnless(verifyClass(IProviderData, self._cut))

    def test_setitem(self):
        context = User()
        obj = self._cut(context)
        obj['one'] = {'one': 1}
        self.assertIsInstance(obj['one'], OOBTree)


class PASProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = testing.setUp()

    def tearDown(self):
        testing.tearDown()

    @property
    def _cut(self):
        from arche_pas.models import PASProvider
        return PASProvider

    def _dummy_provider(self):
        class DummyProvider(self._cut):
            name = 'dummy'
            title = 'Wakka'
            settings = None
            id_key = 'dummy_key'
            default_settings = {'one': 1}

        return DummyProvider

    def test_verify_object(self):
        request = testing.DummyRequest()
        self.failUnless(verifyObject(IPASProvider, self._cut(request)))

    def test_verify_class(self):
        self.failUnless(verifyClass(IPASProvider, self._cut))

    def test_settings(self):
        factory = self._dummy_provider()
        factory.update_settings({'two': 2}, three=3)
        obj = factory(testing.DummyModel())
        self.assertEqual(obj.settings, {'one': 1, 'two': 2, 'three': 3})

    def test_settings_update_provider(self):
        factory = self._dummy_provider()
        factory.update_settings({'two': 2, 'provider': {'title': 'Hello'}})
        obj = factory(testing.DummyModel())
        self.assertEqual(obj.title, 'Hello')

    def test_validate_settings_error(self):
        factory = self._dummy_provider()
        factory.update_settings(one=2)
        self.assertRaises(ProviderConfigError, factory.validate_settings)

    def test_validate_settings_default(self):
        factory = self._dummy_provider()
        factory.update_settings({
            'client_id': 'client_id',
            'auth_uri': 'auth_uri',
            'token_uri': 'token_uri',
            'client_secret': 'client_secret'
        })
        self.assertEqual(factory.validate_settings(), None)

    def test_callback_url(self):
        self.config.include('betahaus.viewcomponent')
        self.config.include('arche_pas.views')
        factory = self._dummy_provider()
        request = Request.blank('/')
        obj = factory(request)
        self.assertEqual(obj.callback_url(), 'http://localhost/pas_callback/dummy')

    def test_get_id(self):
        self.config.include('arche_pas.models')
        user = User()
        provider_data = IProviderData(user)
        provider_data['dummy'] = {'dummy_key': 'very_secret'}
        obj = self._dummy_provider()(testing.DummyModel())
        self.assertEqual(obj.get_id(user), 'very_secret')

    def test_get_user(self):
        self.config.include('arche.testing')
        self.config.include('arche.testing.catalog')
        self.config.include('arche_pas.catalog')
        self.config.include('arche_pas.models')
        root = barebone_fixture(self.config)
        request = testing.DummyRequest()
        self.config.begin(request)
        apply_request_extensions(request)
        request.root = root
        user = User()
        provider_data = IProviderData(user)
        provider_data['dummy'] = {'dummy_key': 'very_secret'}
        provider = self._dummy_provider()
        self.config.registry.registerAdapter(provider, name=provider.name)
        root['users']['jane'] = user
        query = "pas_ident == ('dummy', 'very_secret')"
        docids = root.catalog.query(query)[1]
        self.assertEqual(tuple(request.resolve_docids(docids))[0], user)
        obj = provider(request)
        self.assertEqual(obj.get_user('very_secret'), user)

    # def test_build_reg_case_params(self):
    #     request = testing.DummyRequest()
    #     factory = self._dummy_provider()
    #     obj = factory(request)
    #     data = {
    #
    #     }
    #     obj.build_reg_case_params(data)

    # def prepare_register(self, request, data):
    #
    # def login(self, user, request, first_login = False, came_from = None):
    #

    def test_login(self):
        from arche.resources import User
        request = testing.DummyRequest()
        root = barebone_fixture(self.config)
        root['users']['jane'] = user = User()

        L = []
        def subscriber(event):
            L.append(event)

        self.config.add_subscriber(subscriber, IWillLoginEvent)
        factory = self._dummy_provider()
        obj = factory(request)
        obj.login(user)
        self.assertEqual(L[0].user, user)

    def test_store(self):
        self.config.include('arche.testing')
        self.config.include('arche.testing.catalog')
        self.config.include('arche_pas.catalog')
        self.config.include('arche_pas.models')
        root = barebone_fixture(self.config)
        request = testing.DummyRequest()
        apply_request_extensions(request)
        request.root = root
        self.config.begin(request)
        user = User()
        provider_data = IProviderData(user)
        provider_data['dummy'] = {'dummy_key': 'very_secret'}
        provider = self._dummy_provider()
        self.config.registry.registerAdapter(provider, name=provider.name)
        root['users']['jane'] = user
        obj = provider(request)
        L = []

        def subsc(obj, event):
            L.append(event)

        self.config.add_subscriber(subsc, [IUser, IObjectUpdatedEvent])
        obj.store(user, {'hello': 'world', 1: 2})
        self.assertIn('pas_ident', L[0].changed)

    def test_store_saves_new_keys(self):
        self.config.include('arche.testing')
        self.config.include('arche.testing.catalog')
        self.config.include('arche_pas.models')
        self.config.include('arche_pas.catalog')
        root = barebone_fixture(self.config)
        request = testing.DummyRequest()
        apply_request_extensions(request)
        request.root = root
        self.config.begin(request)
        user = User()
        provider_data = IProviderData(user)
        provider_data['dummy'] = {'dummy_key': 'very_secret'}
        provider = self._dummy_provider()
        self.config.registry.registerAdapter(provider, name=provider.name)
        root['users']['jane'] = user
        obj = provider(request)
        self.assertEqual(obj.store(user, {'hello': 'world', 1: 2}), set(['hello', 1]))
        self.assertEqual(obj.store(user, {'hello': 'world', 1: 2}), set())
        # hello removed
        self.assertEqual(obj.store(user, {1: 2}), set())
        self.assertNotIn('hello', provider_data['dummy'])
        # 1 was updated
        self.assertEqual(obj.store(user, {1: 3}), set([1]))


class AddPASTests(unittest.TestCase):

    def setUp(self):
        self.config = testing.setUp()

    def tearDown(self):
        testing.tearDown()

    @property
    def _fut(self):
        from arche_pas.models import add_pas
        return add_pas

    # FIXME: Proper tests for add_pas


class RegistrationCaseTests(unittest.TestCase):

    def setUp(self):
        self.config = testing.setUp()

    def tearDown(self):
        testing.tearDown()

    @property
    def _cut(self):
        from arche_pas.models import RegistrationCase
        return RegistrationCase

    def test_cmp_crit(self):
        def hello():
            pass

        one = self._cut('one', callback=hello)
        two = self._cut('two', callback=hello)
        self.assertRaises(ValueError, one.cmp_crit, two)


class GetRegisterCaseTests(unittest.TestCase):
    def setUp(self):
        self.config = testing.setUp()
        self.config.include('arche_pas.models')
        self.config.include('arche_pas.registration_cases')

    def tearDown(self):
        testing.tearDown()

    @property
    def _fut(self):
        from arche_pas.models import get_register_case
        return get_register_case

    def test_case_1(self):
        match_params = dict(
            require_authenticated=None,  # Irrelevant alternative
            email_validated_provider=True,
            email_validated_locally=True,
            user_exist_locally=True,  # Irrelevant alternative, must always exist
            email_from_provider=True,
            provider_validation_trusted=True,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case1')
        match_params['require_authenticated'] = False
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case1')
        match_params['require_authenticated'] = True
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case1')
        match_params['user_exist_locally'] = True  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case1')

    def test_case_2(self):
        match_params = dict(
            require_authenticated=True,
            email_validated_provider=True,
            email_validated_locally=False,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=True,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case2')

    def test_case_3(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=True,
            email_validated_locally=False,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=True,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case3')

    def test_case_4(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=True,
            # email_validated_locally=None, #Irrelevant, since user shouldn't exist
            user_exist_locally=False,
            email_from_provider=True,
            provider_validation_trusted=True,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case4')
        match_params['email_validated_locally'] = False  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case4')

    def test_case_5(self):
        match_params = dict(
            require_authenticated=True,
            email_validated_provider=True,
            # email_validated_locally=None, #Shouldn't matter, since user didn't match
            user_exist_locally=False,
            email_from_provider=True,
            provider_validation_trusted=True,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case5')
        match_params['email_validated_locally'] = False  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case5')
        match_params['email_validated_locally'] = True  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case5')

    def test_case_6(self):
        match_params = dict(
            require_authenticated=True,
            email_validated_provider=False,
            email_validated_locally=True,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=False,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case6')

    def test_case_7(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=False,
            email_validated_locally=True,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=False,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case7')

    def test_case_8(self):
        match_params = dict(
            require_authenticated=True,
            email_validated_provider=True,  # Irrelevant
            email_validated_locally=False,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=False,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case8')
        match_params['email_validated_provider'] = False  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case8')

    def test_case_9(self):
        match_params = dict(
            require_authenticated=True,
            # email_validated_provider=True,
            email_validated_locally=False,
            user_exist_locally=False,
            email_from_provider=True,
            provider_validation_trusted=False,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case9')

    def test_case_10(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=True,  # Irrelevant
            email_validated_locally=False,
            user_exist_locally=True,
            email_from_provider=True,
            provider_validation_trusted=False,
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case10')
        match_params['email_validated_provider'] = False  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case10')

    def test_case_11(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=False,  # Irrelevant
            email_validated_locally=False,
            user_exist_locally=False,
            email_from_provider=True,
            provider_validation_trusted=False,  # Should work regardless
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case11')
        match_params['email_validated_provider'] = True  # Shouldn't matter
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case11')

    def test_case_12(self):
        match_params = dict(
            require_authenticated=True,
            email_validated_provider=False,  # Irrelevant
            email_validated_locally=False,  # Check both
            user_exist_locally=False,
            email_from_provider=False,
            provider_validation_trusted=False,  # Should work regardless
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case12')
        # Check all irrelevant options
        match_params_alt = match_params.copy()
        match_params_alt['email_validated_provider'] = True
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case12')

        match_params_alt = match_params.copy()
        match_params_alt['provider_validation_trusted'] = True
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case12')

    def test_case_13(self):
        match_params = dict(
            require_authenticated=False,
            email_validated_provider=False,  # Irrelevant
            email_validated_locally=False,  # Check both
            user_exist_locally=False,
            email_from_provider=False,
            provider_validation_trusted=False,  # Should work regardless
        )
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case13')
        # Check all irrelevant options
        match_params_alt = match_params.copy()
        match_params_alt['email_validated_provider'] = True
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case13')

        match_params_alt = match_params.copy()
        match_params_alt['provider_validation_trusted'] = True
        util = self._fut(registry=self.config.registry, **match_params)
        self.assertEqual(util.name, 'case13')
