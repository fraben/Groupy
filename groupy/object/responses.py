from ..api import status
from ..api import errors
from ..api import endpoint
from .listers import FilterList, MessagePager
from .attachments import AttachmentFactory

from datetime import datetime
from collections import Counter

import time

__all__ = ['Recipient', 'Group', 'Member', 'Message', 'Bot', 'User']

class ApiResponse(object):
    """Base class for all API responses.

    .. note::

        All keyword arguments become properties.

    """
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class Recipient(ApiResponse):
    """Base class for Group and Member.

    Recipients can post and recieve messages.

    :param endpoint: the API endpoint for messages
    :type endpoint: :class:`~groupy.api.endpoint.Endpoint`
    :param str mkey: the :class:`dict` key under which the endpoint returns
        messages
    :param str idkey: the :class:`dict` key whose value represents the key for
        posting and retrieving messages
    """
    def __init__(self, endpoint, mkey, idkey, **kwargs):
        self._endpoint = endpoint
        self._mkey = mkey
        self._idkey = kwargs.get(idkey)
        self.message_count = kwargs.pop('count', 0)
        super().__init__(**kwargs)

    # Splits text into chunks so that each is less than the chunk_size.
    @staticmethod
    def _chunkify(text, chunk_size=450):
        if text is None:
            return [None]
        chunks = []
        while len(text) > chunk_size:
            portion = text[:chunk_size]
            # Find the index of the final whitespace character.
            i = len(portion.rsplit(None, 1)[0])
            # Append the chunk up to that character.
            chunks.append(portion[:i].strip())
            # Re-assign the text to all but the appended chunk.
            text = text[i:].strip()
        chunks.append(text)
        return chunks

    def __len__(self):
        """Return the number of messages in the recipient.
        """
        return self.message_count

    def post(self, text, *attachments):
        """Post a message to the recipient.

        Although the API limits messages to 450 characters, this method will
        split the text component into as many as necessary and include the
        attachments in the final message. Note that a list of messages sent is
        always returned, even if it contains only one element.

        :param str text: the message text
        :param attachments: the attachments to include
        :type attachments: :class:`list`
        :returns: a list of raw API responses (sorry!)
        :rtype: :class:`list`
        """
        if not text and not attachments:
            raise ValueError('must be one attachment or text')
        *chunks, last = self._chunkify(text)
        sent = []
        for chunk in chunks:
            sent.append(self._endpoint.create(self._idkey, chunk))
        sent.append(self._endpoint.create(self._idkey, last, *attachments))
        return sent

    def messages(self, before=None, since=None, after=None, limit=None):
        """Return a page of messages from the recipient.

        :param str before: a reference message ID
        :param str since: a reference message ID
        :param str after: a reference message ID
        :param int limit: maximum number of messages to include in the page
        :returns: a page of messages
        :rtype: :class:`~groupy.object.listers.MessagePager`
        """
        # Messages obtained with the 'after' parameter are in reversed order.
        backward = after is not None
        # Fetch the messages.
        try:
            r = self._endpoint.index(self._idkey, before_id=before,
                                    since_id=since, after_id=after)
        except errors.InvalidResponseError as e:
            # NOT_MODIFIED, in this case, means no more messages.
            if e.args[0].status_code == status.NOT_MODIFIED:
                return None
            raise e
        # Update the message count and grab the messages.
        self.message_count = r['count']
        messages = (Message(self, **m) for m in r[self._mkey])
        return MessagePager(self, messages, backward=backward)


class Group(Recipient):
    """A GroupMe group.
    """
    def __init__(self, **kwargs):
        messages = kwargs.pop('messages', {})
        members = kwargs.pop('members')
        super().__init__(endpoint.Messages, 'messages', 'id', **kwargs)

        self.id = kwargs.get('id')
        self.group_id = kwargs.get('group_id')
        self.name = kwargs.get('name')
        self.type = kwargs.get('type')
        self.description = kwargs.get('description')
        self.image_url = kwargs.get('image_url')
        self.creator_user_id = kwargs.get('creator_user_id')
        self.created_at = datetime.fromtimestamp(kwargs.get('created_at'))
        self.updated_at = datetime.fromtimestamp(kwargs.get('updated_at'))
        ca = messages.get('last_message_created_at')
        if ca >= 0:
            self.last_message_created_at = datetime.fromtimestamp(ca)
            self.last_message_id = messages.get('last_message_id')
        else:
            self.last_message_created_at = None
            self.last_message_id = None
        self.message_count = messages.get('count')
        self._members = [Member(**m) for m in members]
        self.share_url = kwargs.get('share_url')
        
        # Undocumented properties.
        # 'max_members' for groups, 'max_memberships' for former groups.
        for k in ['max_members', 'max_memberships']:
            if k in kwargs:
                self.max_members = kwargs[k]
                break
        else:
            self.max_members = None
        self.office_mode = kwargs.get('office_mode')
        self.phone_number = kwargs.get('phone_number')

    def __repr__(self):
        return "{}, {}/{} members, {} messages".format(
            self.name, len(self.members()),
            self.max_members, self.message_count)

    @classmethod
    def list(cls, former=False):
        """List all of your current or former groups.

        :param former: ``True`` if former groups should be listed, 
            ``False`` (default) lists current groups
        :type former: :obj:`bool`
        :returns: a list of groups
        :rtype: :class:`~groupy.object.listers.FilterList`
        """
        # Former groups come as a single page.
        if former:
            groups = endpoint.Groups.index(former=True)
            return FilterList(Group(**g) for g in groups)
        # Current groups are listed in pages.
        page = 1
        groups = []
        next_groups = endpoint.Groups.index(page=page)
        while next_groups:
            groups.extend(next_groups)
            page += 1
            try:
                next_groups = endpoint.Groups.index(page=page)
            except errors.InvalidResponseError:
                next_groups = None
        return FilterList(Group(**g) for g in groups)

    def refresh(self):
        """Refresh the group information from the API.
        """
        self.__init__(**endpoint.Groups.show(self.id))

    def update(self, name=None, description=None, image_url=None, share=None):
        """Change group information.

        :param str name: the new name of the group
        :param str description: the new description of the group
        :param str image_url: the URL for the new group image
        :param share: whether to generate a share URL
        :type share: :obj:`bool`
        """
        endpoint.Groups.update(name=name, description=description,
                          image_url=image_url, share=share)
        self.refresh()

    def members(self):
        """Return a list of the members in the group.

        :returns: the members of the group
        :rtype: :class:`~groupy.object.listers.FilterList`
        """
        return FilterList(self._members)

    def add(self, *members):
        """Add a member to a group.

        Each member can be either an instance of 
        :class:`~groupy.object.responses.Member` or a :class:`dict` containing 
        ``nickname`` and one of ``email``, ``phone_number``, or ``user_id``.

        :param members: members to add to the group
        :type members: :class:`list`
        :returns: the results ID of the add call
        :rtype: str
        """
        ids = (Member.identify(m) for m in members)
        r = endpoint.Members.add(self.id, *ids)
        return r['results_id']

    def remove(self, member):
        """Remove a member from the group.

        :param member: the member to remove
        :type member: :class:`~groupy.object.responses.Member`
        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: bool
        """
        try:
            endpoint.Members.remove(self.id, member.user_id)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.OK
        return True


class Member(Recipient):
    """A GroupMe member.
    """
    def __init__(self, **kwargs):
        super().__init__(endpoint.DirectMessages, 'direct_messages',
                         'user_id', **kwargs)
        self.id = kwargs.get('id')
        self.user_id = kwargs.get('user_id')
        self.nickname = kwargs.get('nickname')
        self.muted = kwargs.get('muted')
        self.image_url = kwargs.get('image_url')
        self.autokicked = kwargs.get('autokicked')
        self.app_installed = kwargs.get('app_installed')
        self.guid = kwargs.get('guid', None)
        self.message_count = None

    @classmethod
    def list(cls):
        """List all known members regardless of group membership.
        
        :returns: a list of all known members
        :rtype: :class:`~groupy.objects.FilterList`
        """
        groups = Group.list()
        members = {}
        for g in groups:
            for m in g.members():
                if m.user_id not in members:
                    members[m.user_id] = {
                        'member': m,
                        'name': Counter({m.nickname: 1})
                    }
                else:
                    members[m.user_id]['name'][m.nickname] += 1
        renamed = []
        for d in members.values():
            d['member'].nickname = d['name'].most_common()[0][0]
            renamed.append(d['member'])
        return FilterList(renamed)

    def __repr__(self):
        return self.nickname

    @property
    def guid(self):
        if not self._guid:
            self._guid = self._next_guid()
        return self._guid

    @guid.setter
    def guid(self, value):
        self._guid = value

    # Create and return a new guid based on the current time.
    @staticmethod
    def _next_guid():
        return str(time.time())

    def identification(self):
        """Return the identification of the member.

        A member is identified by their ``nickname`` and ``user_id`` properties.
        If the member does not yet have a GUID, a new one is created and
        assigned to them (and is returned alongside the ``nickname`` and
        ``user_id`` properties).

        :returns: the ``nickname``, ``user_id``, and ``guid`` of the member
        :rtype: :class:`dict`
        """
        return {
            'nickname': self.nickname,
            'user_id': self.user_id,
            'guid': self._guid         # new guid set if nonexistant
        }

    @classmethod
    def identify(cls, member):
        """Return or create an identification for a member.

        Member identification is required for adding them to groups. If member
        is a :class:`dict`, it must contain the following keys:

        - ``nickname``
        - ``user_id`` or ``email`` or ``phone_number``
        
        If an identification cannot be created then raise an
        :exc:`~exceptions.AttributeError`.

        :param member: either a :class:`~groupy.object.responses.Member` or a
            :class:`dict` with the required keys
        :returns: the identification of member
        :rtype: :class:`dict`
        :raises AttributeError: if an identication cannot be made
        """
        try:
            return member.identification()
        except AttributeError:
            try:
                for id_type in ['user_id', 'email', 'phone_number']:
                    if id_type in member:
                        if 'guid' not in member:
                            member['guid'] = cls._next_guid()
                        return {
                            'nickname': member['nickname'],
                            'id_type': member[id_type],
                            'guid': member['guid']
                        }
            except AttributeError:
                raise AttributeError('no nickname')
            raise AttributeError('no user_id, email, or phone_number')


class Message(ApiResponse):
    """A GroupMe message.

    :param recipient: the reciever of the message
    :type recipient: :class:`~groupy.object.responses.Recipient`
    """
    _user = None
    
    def __init__(self, recipient, **kwargs):
        super().__init__()
        self._recipient = recipient
        
        self.id = kwargs.get('id')
        self.source_guid = kwargs.get('source_guid')
        self.created_at = datetime.fromtimestamp(kwargs.get('created_at'))
        self.user_id = kwargs.get('user_id')
        self.group_id = kwargs.get('group_id')
        self.recipient_id = kwargs.get('recipient_id')
        self.name = kwargs.get('name')
        self.avatar_url = kwargs.get('avatar_url')
        self.text = kwargs.get('text')
        self.system = kwargs.pop('system', False)
        self.favorited_by = kwargs.get('favorited_by')
        self.attachments = [
            AttachmentFactory.create(**a) for a in kwargs.get('attachments')]
        
        # Determine the conversation id (different for direct messages)
        try:    # assume group message
            self._conversation_id = recipient.group_id
        except AttributeError:  # oops, its a direct message
            if self._user is None:
                self._user = User.get()
            participants = [self._user.user_id, recipient.user_id]
            self._conversation_id = '+'.join(sorted(participants))

    def __repr__(self):
        msg = "{}: {}".format(self.name, self.text or "")
        if self.attachments:
            for a in self.attachments:
                msg += " +<{}>".format(str(a))
        return msg

    def __len__(self):
        """Return the length of the message text.
        """
        return len(self.text)

    def like(self):
        """Like the message.

        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: bool
        """
        try:
            endpoint.Likes.create(self._conversation_id, self.id)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.OK
        return True

    def unlike(self):
        """Unlike the message.

        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: bool
        """
        try:
            endpoint.Likes.destroy(self._conversation_id, self.id)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.OK
        return True

    def likes(self):
        """Return a :class:`~groupy.object.listers.FilterList` of the
        members that like the message.
        
        :returns: a list of the members who "liked" this message
        :rtype: :class:`~groupy.object.listers.FilterList`
        """
        try:
            liked = filter(
                lambda m: m.user_id in self.favorited_by,
                self._recipient.members())
        except AttributeError:
            liked = []
            for i in self.favorited_by:
                if i == self._user.user_id:
                    liked.append(self._user)
                elif i == self.recipient_id:
                    liked.append(self._recipient)
        return FilterList(liked)


class Bot(ApiResponse):
    """A GroupMe bot.

    Each bot belongs to a single group. Messages posted by the bot are always
    posted to the group to which the bot belongs.
    """
    def __init__(self, **kwargs):
        super().__init__()
        self.bot_id = kwargs.get('bot_id')
        self.gorup_id = kwargs.get('group_id')
        self.name = kwargs.get('name')
        self.avatar_url = kwargs.get('avatar_url')
        self.callback_url = kwargs.get('callback_url')

    def __repr__(self):
        return self.name

    @classmethod
    def create(cls, name, group, avatar_url=None, callback_url=None):
        """Create a new bot.
        
        :param str name: the name of the bot
        :param group: the group to which the bot will belong
        :type group: :class:`~groupy.object.responses.Bot`
        :param str avatar_url: the URL for a GroupMe image to be used as the
            bot's avatar
        :param str callback_url: the URL to which each group message will be
            POSTed
        :returns: the new bot
        :rtype: :class:`~groupy.object.responses.Bot`
        """
        bot = endpoint.Bots.create(name, group.group_id, avatar_url, callback_url)
        return cls(**bot)

    @classmethod
    def list(cls):
        """Return a list of your bots.

        :returns: a list of your bots
        :rtype: :class:`~groupy.object.listers.FilterList`
        """
        return FilterList(Bot(**b) for b in endpoint.Bots.index())

    def post(self, text, picture_url=None):
        """Post a message to the group of the bot.

        :param str text: the message text
        :param str picture_url: the GroupMe image URL for an image
        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: bool
        """
        try:
            endpoint.Bot.post(self.bot_id, text, picture_url)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.CREATED
        return True

    def destroy(self):
        """Destroy the bot.

        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: bool
        """
        try:
            endpoint.Bot.destroy(self.bot_id)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.OK
        return True


class User(ApiResponse):
    """A GroupMe user.

    This is you, as determined by your API key.
    """
    def __init__(self, **kwargs):
        super().__init__()
        self.created_at = datetime.fromtimestamp(kwargs.get('created_at'))
        self.udpated_at = datetime.fromtimestamp(kwargs.get('updated_at'))
        self.id = kwargs.get('id')
        self.user_id = kwargs.get('user_id')
        self.name = kwargs.get('name')
        self.email = kwargs.get('email')
        self.phone_number = kwargs.get('phone_number')
        self.image_url = kwargs.get('image_url')
        self.sms = kwargs.get('sms')

    def __repr__(self):
        return self.name
        
    @property
    def nickname(self):
        """Your user name.
        """
        return self.name
    
    @classmethod
    def get(cls):
        """Return your user information.

        :returns: your user information
        :rtype: :class:`dict`
        """
        return cls(**endpoint.Users.me())

    @classmethod
    def enable_sms(cls, duration=4, registration_token=None):
        """Enable SMS mode.

        Enabling SMS mode causes GroupMe to send a text message for each
        message sent to the group.

        :param int duration: the number of hours for which to send text
            messages
        :param str registration_token: the push notification token for
            for which messages should be suppressed; if omitted, the user
            will recieve both push notifications as well as text messages
        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: :obj:`bool`
        """
        try:
            endpoint.Sms.create(duration, registration_token)
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.CREATED
        return True

    @classmethod
    def disable_sms(cls):
        """Disable SMS mode.

        Disabling SMS mode causes push notifications to resume and SMS text
        messages to be discontinued.

        :returns: ``True`` if successful, ``False`` otherwise
        :rtype: :obj:`bool`
        """
        try:
            endpoint.Sms.delete()
        except errors.InvalidResponse as e:
            return e.args[0].status_code == status.OK
        return True

