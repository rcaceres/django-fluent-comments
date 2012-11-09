from django.conf import settings
from django.contrib.comments.moderation import CommentModerator, moderator
from django.contrib.sites.models import get_current_site
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.http import HttpRequest
from django.shortcuts import render
from django.utils.encoding import smart_str
from akismet import Akismet
from fluent_comments import appsettings

# Akismet code originally based on django-comments-spamfighter.

__all__ = (
    'FluentCommentsModerator',
    'moderate_model',
    'get_model_moderator',
    'comments_are_closed',
    'comments_are_moderated',
)


class FluentCommentsModerator(CommentModerator):
    """
    Moderation policy for fluent-comments.
    """
    auto_close_field = None
    auto_moderate_field = None
    enable_field = None

    close_after = appsettings.FLUENT_COMMENTS_CLOSE_AFTER_DAYS
    moderate_after = appsettings.FLUENT_COMMENTS_MODERATE_AFTER_DAYS
    email_notification = appsettings.FLUENT_COMMENTS_USE_EMAIL_MODERATION
    akismet_check = appsettings.FLUENT_CONTENTS_USE_AKISMET
    akismet_check_action = appsettings.FLUENT_COMMENTS_AKISMET_ACTION


    def allow(self, comment, content_object, request):
        """
        Determine whether a given comment is allowed to be posted on a given object.

        Returns ``True`` if the comment should be allowed, ``False`` otherwise.
        """
        # Parent class check
        if not super(FluentCommentsModerator, self).allow(comment, content_object, request):
            return False

        # Akismet check
        if self.akismet_check and self.akismet_check_action == 'delete':
            if self._akismet_check(comment, content_object, request):
                return False  # Akismet marked the comment as spam.

        return True


    def moderate(self, comment, content_object, request):
        """
        Determine whether a given comment on a given object should be allowed to show up immediately,
        or should be marked non-public and await approval.

        Returns ``True`` if the comment should be moderated (marked non-public), ``False`` otherwise.
        """
        # Parent class check
        if super(FluentCommentsModerator, self).moderate(comment, content_object, request):
            return True

        # Akismet check
        if self.akismet_check and self.akismet_check_action == 'moderate':
            # Return True if akismet marks this comment as spam and we want to moderate it.
            if self._akismet_check(comment, content_object, request):
                return True

        return False


    def email(self, comment, content_object, request):
        """
        Send email notification of a new comment to site staff when email notifications have been requested.
        """
        # This code is copied from django.contrib.comments.moderation,
        # since it doesn't offer a RequestContext, making it really hard to generate URL's with FQDN in the email
        if not self.email_notification:
            return

        recipient_list = [manager_tuple[1] for manager_tuple in settings.MANAGERS]
        site = get_current_site(request)
        subject = '[{0}] New comment posted on "{1}"'.format(site.name, content_object)
        context = {
            'site': site,
            'comment': comment,
            'content_object': content_object
        }
        message = render(request, "comments/comment_notification_email.txt", context)
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=True)


    def _akismet_check(self, comment, content_object, request):
        """
        Connects to Akismet and returns True if Akismet marks this comment as
        spam. Otherwise returns False.
        """
        # Get Akismet data
        AKISMET_API_KEY = appsettings.AKISMET_API_KEY
        if not AKISMET_API_KEY:
            raise ImproperlyConfigured('You must set AKISMET_API_KEY to use comment moderation with Akismet.')

        auto_blog_url = '{0}://{1}/'.format(request.is_secure() and 'https' or 'http', get_current_site(request).domain)
        akismet_api = Akismet(
            key=AKISMET_API_KEY,
            blog_url=appsettings.AKISMET_BLOG_URL or auto_blog_url
        )

        if akismet_api.verify_key():
            akismet_data = {
                # Comment info
                'permalink': content_object.get_absolute_url(),
                'comment_type': 'comment',
                'comment_author': getattr(comment, 'name', ''),
                'comment_author_email': getattr(comment, 'email', ''),
                'comment_author_url': getattr(comment, 'url', ''),

                # Request info
                'referrer': request.META.get('HTTP_REFERER', ''),
                'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                'user_ip': comment.ip_address,

                # Server info
                'SERVER_ADDR': request.META.get('SERVER_ADDR', ''),
                'SERVER_ADMIN': request.META.get('SERVER_ADMIN', ''),
                'SERVER_NAME': request.META.get('SERVER_NAME', ''),
                'SERVER_PORT': request.META.get('SERVER_PORT', ''),
                'SERVER_SIGNATURE': request.META.get('SERVER_SIGNATURE', ''),
                'SERVER_SOFTWARE': request.META.get('SERVER_SOFTWARE', ''),
                'HTTP_ACCEPT': request.META.get('HTTP_ACCEPT', ''),
            }

            if akismet_api.comment_check(smart_str(comment.comment), data=akismet_data, build_data=True):
                return True

        return False


def moderate_model(ParentModel, publication_date_field=None, enable_comments_field=None):
    """
    Register a parent model (e.g. ``Blog`` or ``Article``) that should receive comment moderation.

    :param ParentModel: The parent model, e.g. a ``Blog`` or ``Article`` model.
    :param publication_date_field: The field name of a :class:`~django.db.models.DateTimeField` in the parent model which stores the publication date.
    :type publication_date_field: str
    :param enable_comments_field: The field name of a :class:`~django.db.models.BooleanField` in the parent model which stores the whether comments are enabled.
    :type enable_comments_field: str
    """
    attrs = {
        'auto_close_field': publication_date_field,
        'auto_moderate_field': publication_date_field,
        'enable_field': enable_comments_field,
    }
    ModerationClass = type(ParentModel.__name__ + 'Moderator', (FluentCommentsModerator,), attrs)
    moderator.register(ParentModel, ModerationClass)


def get_model_moderator(model):
    """
    Return the moderator class that is registered with a content object.
    If there is no associated moderator with a class, None is returned.

    :param model: The Django model registered with :func:`moderate_model`
    :type model: :class:`~django.db.models.Model`
    :return: The moderator class which holds the moderation policies.
    :rtype: :class:`~django.contrib.comments.moderation.CommentModerator`
    """
    try:
        return moderator._registry[model]
    except KeyError:
        return None


def comments_are_closed(content_object):
    """
    Return whether comments are closed for a given target object.
    """
    moderator = get_model_moderator(content_object.__class__)
    if moderator is None:
        return False

    # Check the 'enable_field', 'auto_close_field' and 'close_after',
    # by reusing the basic Django policies.
    request = HttpRequest()
    return not CommentModerator.allow(moderator, None, content_object, request)


def comments_are_moderated(content_object):
    """
    Return whether comments are moderated for a given target object.
    """
    moderator = get_model_moderator(content_object.__class__)
    if moderator is None:
        return False

    # Check the 'auto_moderate_field', 'moderate_after',
    # by reusing the basic Django policies.
    request = HttpRequest()
    return CommentModerator.moderate(moderator, None, content_object, request)