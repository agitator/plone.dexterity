# -*- coding: utf-8 -*-
from AccessControl import Unauthorized
from Acquisition import aq_base
from Acquisition import aq_inner
from DateTime import DateTime
from plone.app.uuid.utils import uuidToObject
from plone.autoform.interfaces import IFormFieldProvider
from plone.behavior.interfaces import IBehaviorAssignable
from plone.dexterity.interfaces import IDexterityFTI
from plone.dexterity.schema import SCHEMA_CACHE
from plone.dexterity.schema import SchemaNameEncoder  # noqa bbb
from plone.dexterity.schema import portalTypeToSchemaName  # noqa bbb
from plone.dexterity.schema import schemaNameToPortalType  # noqa bbb
from plone.dexterity.schema import splitSchemaName  # noqa bbb
from plone.supermodel.utils import mergedTaggedValueDict
from plone.uuid.interfaces import IUUID
from zope import deprecation
from zope.component import createObject
from zope.component import getUtility
from zope.container.interfaces import INameChooser
from zope.dottedname.resolve import resolve
from zope.event import notify
from zope.lifecycleevent import ObjectCreatedEvent

import datetime
import logging
import textwrap

deprecation.deprecated(
    'SchemaNameEncoder',
    'moved to plone.dexterity.schema')
deprecation.deprecated(
    'portalTypeToSchemaName',
    'moved to plone.dexterity.schema')
deprecation.deprecated(
    'schemaNameToPortalType',
    'moved to plone.dexterity.schema')
deprecation.deprecated(
    'splitSchemaName',
    'moved to plone.dexterity.schema')

log = logging.getLogger(__name__)

# Not thread safe, but downside of a write conflict is very small
_dottedCache = {}


def resolveDottedName(dottedName):
    """Resolve a dotted name to a real object
    """
    global _dottedCache
    if dottedName not in _dottedCache:
        _dottedCache[dottedName] = resolve(dottedName)
    return _dottedCache[dottedName]


def iterSchemataForType(portal_type):
    """XXX: came from plone.app.deco.utils, very similar to iterSchemata

    Not fully merged codewise with iterSchemata as that breaks
    test_webdav.test_readline_mimetype_additional_schemata.
    """
    main_schema = SCHEMA_CACHE.get(portal_type)
    if main_schema:
        yield main_schema
    for schema in getAdditionalSchemata(portal_type=portal_type):
        yield schema


def iterSchemata(context):
    """Return an iterable containing first the object's schema, and then
    any form field schemata for any enabled behaviors.
    """
    main_schema = SCHEMA_CACHE.get(context.portal_type)
    if main_schema:
        yield main_schema
    for schema in getAdditionalSchemata(context=context):
        yield schema


def getAdditionalSchemata(context=None, portal_type=None):
    """Get additional schemata for this context or this portal_type.

    Additional form field schemata can be defined in behaviors.

    Usually either context or portal_type should be set, not both.
    The idea is that for edit forms or views you pass in a context
    (and we get the portal_type from there) and for add forms you pass
    in a portal_type (and the context is irrelevant then).  If both
    are set, the portal_type might get ignored, depending on which
    code path is taken.
    """
    log.debug("getAdditionalSchemata with context %r and portal_type %s",
              context, portal_type)
    if context is None and portal_type is None:
        return
    if context:
        behavior_assignable = IBehaviorAssignable(context, None)
    else:
        behavior_assignable = None
    if behavior_assignable is None:
        log.debug("No behavior assignable found, only checking fti.")
        # Usually an add-form.
        if portal_type is None:
            portal_type = context.portal_type
        for schema_interface in SCHEMA_CACHE.behavior_schema_interfaces(
            portal_type
        ):
            form_schema = IFormFieldProvider(schema_interface, None)
            if form_schema is not None:
                yield form_schema
    else:
        log.debug("Behavior assignable found for context.")
        for behavior_reg in behavior_assignable.enumerateBehaviors():
            form_schema = IFormFieldProvider(behavior_reg.interface, None)
            if form_schema is not None:
                yield form_schema


def createContent(portal_type, **kw):
    fti = getUtility(IDexterityFTI, name=portal_type)
    content = createObject(fti.factory)

    # Note: The factory may have done this already, but we want to be sure
    # that the created type has the right portal type. It is possible
    # to re-define a type through the web that uses the factory from an
    # existing type, but wants a unique portal_type!
    content.portal_type = fti.getId()
    schemas = iterSchemataForType(portal_type)
    fields = dict(kw)  # create a copy

    for schema in schemas:
        # schema.names() doesn't return attributes from superclasses in derived
        # schemas. therefore we have to iterate over all items from the passed
        # keywords arguments and set it, if the behavior has the questioned
        # attribute.
        behavior = schema(content)
        for name, value in fields.items():
            try:
                # hasattr swallows exceptions.
                if getattr(behavior, name):
                    setattr(behavior, name, value)
                    del fields[name]
            except AttributeError:
                # fieldname not available
                pass

    for (key, value) in fields.items():
        setattr(content, key, value)

    notify(ObjectCreatedEvent(content))
    return content


def addContentToContainer(container, object, checkConstraints=True):
    """Add an object to a container.

    The portal_type must already be set correctly. If checkConstraints
    is False no check for addable content types is done. The new object,
    wrapped in its new acquisition context, is returned.
    """
    if not hasattr(aq_base(object), "portal_type"):
        raise ValueError("object must have its portal_type set")

    container = aq_inner(container)
    if checkConstraints:
        container_fti = container.getTypeInfo()

        fti = getUtility(IDexterityFTI, name=object.portal_type)
        if not fti.isConstructionAllowed(container):
            raise Unauthorized("Cannot create %s" % object.portal_type)

        if container_fti is not None \
           and not container_fti.allowType(object.portal_type):
            raise ValueError(
                "Disallowed subobject type: %s" % object.portal_type
            )

    name = getattr(aq_base(object), 'id', None)
    name = INameChooser(container).chooseName(name, object)
    object.id = name

    newName = container._setObject(name, object)
    try:
        return container._getOb(newName)
    except AttributeError:
        uuid = IUUID(object)
        return uuidToObject(uuid)


def createContentInContainer(container, portal_type, checkConstraints=True,
                             **kw):
    content = createContent(portal_type, **kw)
    return addContentToContainer(
        container,
        content,
        checkConstraints=checkConstraints
    )


def safe_utf8(s):
    if isinstance(s, unicode):
        s = s.encode('utf8')
    return s


def safe_unicode(s):
    if isinstance(s, str):
        s = s.decode('utf8')
    return s


def datify(s):
    """Get a DateTime object from a string (or anything parsable by DateTime,
       a datetime.date, a datetime.datetime
    """
    if not isinstance(s, DateTime):
        if s == 'None':
            s = None
        elif isinstance(s, datetime.datetime):
            s = DateTime(s.isoformat())
        elif isinstance(s, datetime.date):
            s = DateTime(s.year, s.month, s.day)
        elif s is not None:
            s = DateTime(s)

    return s


def all_merged_tagged_values_dict(ifaces, key):
    """mergedTaggedValueDict of all interfaces for a given key

    usally interfaces is a list of schemas
    """
    info = dict()
    for iface in ifaces:
        info.update(mergedTaggedValueDict(iface, key))
    return info


class BehaviorInfo(object):
    """Helper class for debugging dexterity type behaviors
    """

    def __init__(self, context):
        self.context = context

    def indent(self, text, ind=4, width=80):
        """Text indentation helper.
        """
        return textwrap.fill(text, width, initial_indent=ind*u' ')

    def __repr__(self):
        lines = list()
        behavior_assignable = IBehaviorAssignable(self.context, [])
        for behavior_reg in behavior_assignable.enumerateBehaviors():
            lines.append(u'Behavior {0} ({1}):'.format(
                behavior_reg.interface.__identifier__,
                behavior_reg.name
            ))
            lines.append(self.indent(behavior_reg.title))
            if behavior_reg.description:
                lines.append(self.indent(behavior_reg.description, ind=8))
            if behavior_reg.marker is not None \
                    and behavior_reg.marker is not behavior_reg.interface:
                lines.append(self.indent(
                    u'Marker: {0}'.format(behavior_reg.marker.__identifier__)
                ))
            if behavior_reg.factory:
                lines.append(self.indent(
                    u'Factory: {0}'.format(unicode(behavior_reg.factory))
                ))
            form_schema = IFormFieldProvider(behavior_reg.interface, None)
            if form_schema is None:
                continue
            import pdb;pdb.set_trace()
            lines.append(self.indent(u'Fields:'))
            lines.append(u'')
        return u'\n'.join(lines)
