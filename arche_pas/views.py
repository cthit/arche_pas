# -*- coding: utf-8 -*-
from arche_pas import _
from arche_pas import logger

import deform
from arche.events import ObjectUpdatedEvent
from arche.interfaces import IEmailValidationTokens
from arche.interfaces import IUser
from arche.interfaces import IViewInitializedEvent
from arche.security import PERM_EDIT
from arche.utils import get_content_schemas
from arche.views.auth import LoginForm
from arche.views.auth import RegisterForm
from arche.views.base import BaseForm
from arche.views.base import BaseView
from arche.views.exceptions import ExceptionView
from oauthlib.oauth2 import OAuth2Error
from pyramid.httpexceptions import HTTPBadRequest
from pyramid.httpexceptions import HTTPForbidden
from pyramid.httpexceptions import HTTPFound
from pyramid.httpexceptions import HTTPNotFound
from pyramid.renderers import render
from six import string_types
from transaction import commit
from zope.component.event import objectEventNotify
from zope.interface.interfaces import ComponentLookupError

from arche_pas.interfaces import IPASProvider
from arche_pas.interfaces import IProviderData
from arche_pas.models import UnknownProvider


class BeginAuthView(BaseView):

    def __call__(self):
        provider_name = self.request.matchdict.get('provider', '')
        provider = self.request.registry.queryAdapter(self.request, IPASProvider, name = provider_name)
        came_from = self.request.GET.get('came_from', '')
        if came_from:
            self.request.session['came_from'] = came_from
        if provider:
            redirect_url = provider.begin()
            logger.debug('Begin redirects to: %s', redirect_url)
            return HTTPFound(location=redirect_url)
        raise HTTPNotFound(_("No login provider with that name"))


class CallbackAuthView(BaseView):

    def __call__(self):
        provider_name = self.request.matchdict.get('provider', '')
        provider = self.request.registry.queryAdapter(self.request, IPASProvider, name = provider_name)
        profile_data = provider.callback()
        user_ident = profile_data.get(provider.id_key, None)
        if not user_ident:
            raise HTTPBadRequest("Profile response didn't contain a user identifier.")
        user = provider.get_user(user_ident)
        if user:
            provider.logger.info('Logged in %s via provider %s', user.userid, provider_name)
            self.flash_messages.add(_("Logged in via ${provider}",
                                      mapping={'provider': self.request.localizer.translate(provider.title)}),
                                    type='success')
            provider.store(user, profile_data)
            came_from = self.request.session.pop('came_from', None)
            return provider.login(user, came_from=came_from)
        else:
            provider.logger.info('Rendering registration via provider %s', provider_name)
            reg_response = provider.prepare_register(profile_data)
            if isinstance(reg_response, string_types):
                return HTTPFound(
                    location = self.request.route_url('pas_register',
                                                      provider = provider.name,
                                                      reg_id = reg_response)
                )
            else:
                return reg_response


class RegisterPASForm(BaseForm):
    type_name = u'Auth'
    schema_name = 'register_finish'
    title = _(u"Complete registration")

    def __init__(self, context, request):
        super(RegisterPASForm, self).__init__(context, request)
        if request.authenticated_userid != None:
            raise HTTPForbidden(_(u"Already logged in."))

    @property
    def buttons(self):
        return (deform.Button('register', title = _("Register"), css_class = 'btn btn-primary'),
                 self.button_cancel,)

    @property
    def provider(self):
        provider_name = self.request.matchdict.get('provider', '')
        try:
            return self.request.registry.getAdapter(self.request, IPASProvider, name = provider_name)
        except ComponentLookupError:
            raise HTTPNotFound("No provider named %s" % provider_name)

    @property
    def reg_id(self):
        return self.request.matchdict.get('reg_id', '')

    @property
    def provider_response(self):
        data = self.request.session.get(self.reg_id, None)
        if not data:
            raise HTTPBadRequest("No session data found from provider")
        return data

    def appstruct(self):
        return self.provider.registration_appstruct(self.provider_response)

    def get_schema(self):
        schema = get_content_schemas(self.request.registry)[self.type_name][self.schema_name]()
        for k in ('email', 'password'):
            if k in schema:
                del schema[k]
        return schema

    def register_success(self, appstruct):
        factory = self.request.content_factories['User']
        userid = appstruct.pop('userid')
        redirect_url = self.request.session.pop('came_from', None)
        schema_redirect_url = appstruct.pop('came_from', None)
        if not redirect_url:
            redirect_url = schema_redirect_url
        email = self.provider.get_email(self.provider_response)
        if email:
            user = factory(email = email, **appstruct)
        else:
            user = factory(**appstruct)
        self.context['users'][userid] = user
        #Trust email validation?
        require_validation = True
        if self.provider.trust_email:
            if bool(self.provider.get_email(self.provider_response, validated=True)):
                user.email_validated = True
                require_validation = False
        if require_validation:
            if email:
                #FIXME: This notification may move to the adapter in arche.
                val_tokens = IEmailValidationTokens(user)
                token = val_tokens.new(email)
                url = self.request.resource_url(user, '_ve', query = {'t': token, 'e': email})
                html = self.render_template("arche:templates/emails/email_validate.pt", user = user, url = url)
                self.request.send_email(_("Email validation"),
                                        [email],
                                        html)
                self.flash_messages.add(
                    _("registered_but_needs_validation",
                      default="You're registered but you still need to validate your email address. "
                      "An email with a link has been sent to ${email}. "
                      "Click the link to complete the proceedure. "
                      "If you wish to change your email you may do so on your profile page.",
                      mapping={'email': email}),
                    type="info", auto_destruct=False
                )
            else:
                self.flash_messages.add(
                    _("registered_no_email",
                      default="You're registered, but no email was found. Set one on your profile."),
                    type="success",
                )
        else:
            self.flash_messages.add(_("Welcome, you're now registered!"), type="success")
        self.provider.store(user, self.provider_response)
        commit()  # We want potential conflicts to be checked here. In case of errors this will abort login
        self.request.session.pop(self.reg_id, None)
        return self.provider.login(user, first_login = True, came_from = redirect_url)


class ConfirmLinkAccountPASForm(BaseForm):
    type_name = 'PAS'
    schema_name = 'link_data'

    @property
    def buttons(self):
        return (deform.Button('link', title = _("Link account"),),
                self.button_cancel,)

    def __init__(self, context, request):
        super(ConfirmLinkAccountPASForm, self).__init__(context, request)
        if not request.authenticated_userid:
            raise HTTPForbidden(_("You need to be logged in to link an account"))
        self.provider_response #To provoke test

    @property
    def provider(self):
        provider_name = self.request.matchdict.get('provider', '')
        try:
            return self.request.registry.getAdapter(self.request, IPASProvider, name = provider_name)
        except ComponentLookupError:
            raise HTTPNotFound("No provider named %s" % provider_name)

    @property
    def reg_id(self):
        return self.request.matchdict.get('reg_id', '')

    @property
    def provider_response(self):
        data = self.request.session.get(self.reg_id, None)
        if not data:
            raise HTTPBadRequest("No session data found from provider. You may need to restart the procedure.")
        return data

    def link_success(self, appstruct):
        self.provider.store(self.request.profile, self.provider_response)
        #FIXME: Decide about overwrite of email
        #Maybe flag email as validated?
        if not self.request.profile.email_validated:
            #Do we trust the provider and have a proper email address?
            email = self.provider.get_email(self.provider_response)
            if email and email == self.profile.email and self.provider.trust_email:
                self.request.profile.email_validated = True
        provider_title = self.request.localizer.translate(self.provider.title)
        self.flash_messages.add(_("You may now login with ${provider_title}.",
                                  mapping={'provider_title': provider_title}),
                                type="success")
        self.request.session.pop(self.reg_id, None)
        # Treat this as a login, and fire that event
        self.provider.notify_login(self.request.profile, first_login=False)
        redirect_url = self.request.session.pop('came_from', None)
        if redirect_url:
            return HTTPFound(location=redirect_url)
        return HTTPFound(location=self.request.resource_url(self.context))


class RemovePASDataForm(BaseForm):
    type_name = 'PAS'
    schema_name = 'remove_data'
    title = _("Remove data")

    @property
    def buttons(self):
        return (deform.Button('remove', title = _("Remove"), css_class = 'btn btn-danger'),
                 self.button_cancel,)

    def remove_success(self, appstruct):
        if appstruct.get('remove_password', False) == True:
            self.context.password = None
        if appstruct['providers_to_remove']:
            provider_data = IProviderData(self.context)
            for provider_name in appstruct['providers_to_remove']:
                del provider_data[provider_name]
            event = ObjectUpdatedEvent(self.context, changed = ['pas_ident'])
            objectEventNotify(event)
            self.flash_messages.add(_("Removed successfully"), type='success')
        return HTTPFound(location=self.request.resource_url(self.context, 'pas_linked_accounts'))

    def cancel_success(self, *args):
        return HTTPFound(location=self.request.resource_url(self.context, 'pas_linked_accounts'))
    cancel_failure = cancel_success



class RedirectOnExceptionView(ExceptionView):

    def __call__(self):
        response = super(RedirectOnExceptionView, self).__call__()
        if response.get('debug', None):
            return response
        self.flash_messages.add(_("Something went wrong during login. Try again."),
                                require_commit=False, type='danger')
        return HTTPFound(location=self.request.resource_url(self.context))


class LinkedAccountsInfo(BaseView):

    def __call__(self):
        provider_data = IProviderData(self.context)
        providers = dict(self.request.registry.getAdapters((self.request,), IPASProvider))
        linked_providers = []
        for name in provider_data:
            linked_providers.append(providers.get(name, UnknownProvider(name)))
        linked_providers.sort(key=lambda x: x.title.lower())
        unlinked_providers = []
        for name in providers:
            if name not in provider_data:
                unlinked_providers.append(providers[name])
        unlinked_providers.sort(key=lambda x: x.title.lower())
        return {'linked_providers': linked_providers,
                'unlinked_providers': unlinked_providers,
                'provider_data': provider_data}


def linked_accounts_menu_item(context, request, va, **kw):
    """
    Render menu item in profile.
    """
    return """
    <li><a href="%s">%s</a></li>
    """ % (request.resource_url(request.profile, 'pas_linked_accounts'),
           request.localizer.translate(va.title))


def inject_login_providers(view, event):
    """ Render login provider buttons before the login form. """
    return inject_providers(view, "arche_pas:templates/providers_login.pt")


def inject_register_providers(view, event):
    """ Render registration provider buttons before the reg form. """
    return inject_providers(view, "arche_pas:templates/providers_register.pt")


def inject_providers(view, tpl):
    if not view.form_options.get('before_fields'):
        view.form_options['before_fields'] = ""
    request = view.request
    providers = tuple(request.registry.getAdapters((request,), IPASProvider))
    values = {'providers': providers}
    view.form_options['before_fields'] += render(tpl, values, request=request)

def includeme(config):
    config.add_route('pas_begin', '/pas_begin/{provider}')
    config.add_view(BeginAuthView, route_name='pas_begin')
    config.add_route('pas_callback', '/pas_callback/{provider}')
    config.add_view(CallbackAuthView, route_name='pas_callback')
    config.add_route('pas_register', '/pas_register/{provider}/{reg_id}')
    config.add_view(RegisterPASForm, route_name='pas_register',
                    renderer='arche:templates/form.pt')
    config.add_view(RemovePASDataForm, context=IUser, name='remove_pas',
                    renderer='arche:templates/form.pt', permission=PERM_EDIT)
    config.add_route('pas_link', '/pas_link/{provider}/{reg_id}')
    config.add_view(ConfirmLinkAccountPASForm, route_name='pas_link',
                    renderer='arche_pas:templates/link_form.pt')
    config.add_exception_view(
        RedirectOnExceptionView,
        context=OAuth2Error,
        xhr=False,
        renderer="arche_pas:templates/oauth_exception.pt")
    config.add_view(LinkedAccountsInfo, context=IUser, name='pas_linked_accounts',
                    renderer='arche_pas:templates/linked_accounts.pt', permission=PERM_EDIT)
    config.add_view_action(
        linked_accounts_menu_item, 'user_menu', 'pas_linked_accounts',
        title=_("Linked accounts"), priority=30
    )
    config.add_subscriber(inject_login_providers, [LoginForm, IViewInitializedEvent])
    config.add_subscriber(inject_register_providers, [RegisterForm, IViewInitializedEvent])
