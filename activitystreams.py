"""ActivityStreams API handler classes.

Implements the OpenSocial ActivityStreams REST API:
http://opensocial-resources.googlecode.com/svn/spec/2.0.1/Social-API-Server.xml#ActivityStreams-Service
http://opensocial-resources.googlecode.com/svn/spec/2.0.1/Core-Data.xml

Request paths are of the form /user_id/group_id/app_id/activity_id, where
each element is optional. user_id may be @me. group_id may be @all, @friends
(currently identical to @all), or @self. app_id may be @app, but it doesn't
matter, it's currently ignored.

The supported query parameters are startIndex and count, which are handled as
described in OpenSocial (above) and OpenSearch.

Other relevant activity REST APIs:
http://status.net/wiki/Twitter-compatible_API
http://wiki.activitystrea.ms/w/page/25347165/StatusNet%20Mapping
https://developers.google.com/+/api/latest/activities/list

ActivityStreams specs:
http://activitystrea.ms/specs/
"""

__author__ = ['Ryan Barrett <activitystreams@ryanb.org>']

import json
import logging
import re
import os
import urllib
from webob import exc

import appengine_config
import facebook
import instagram
import source
import twitter
from webutil import util

from google.appengine.ext.webapp import template
import webapp2

# maps app id to source class
SOURCE = {
  'facebook-activitystreams': facebook.Facebook,
  'instagram-activitystreams': instagram.Instagram,
  'twitter-activitystreams': twitter.Twitter,
  }.get(appengine_config.APP_ID)

XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<response>%s</response>
"""
ATOM_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), 'templates', 'user_feed.atom')
ITEMS_PER_PAGE = 100

# default values for each part of the API request path, e.g. /@me/@self/@all/...
PATH_DEFAULTS = ((source.ME,), (source.ALL, source.FRIENDS), (source.APP,), ())
MAX_PATH_LEN = len(PATH_DEFAULTS)


class Handler(webapp2.RequestHandler):
  """Base class for ActivityStreams API handlers.

  Attributes:
    source: Source subclass
  """
  handle_exception = instagram.handle_exception

  def source_class(self):
    """Return the Source subclass to use. May be overridden by subclasses."""
    return SOURCE

  def get(self):
    """Handles an API GET.

    Request path is of the form /user_id/group_id/app_id/activity_id , where
    each element is an optional string object id.
    """
    args = {key: self.request.params[key]
            for key in ('access_token', 'access_token_key', 'access_token_secret')
            if key in self.request.params}
    source = self.source_class()(**args)

    # parse path
    args = urllib.unquote(self.request.path).strip('/').split('/')
    if len(args) > MAX_PATH_LEN:
      raise exc.HTTPNotFound()
    elif args == ['']:
      args = []

    # handle default path elements
    args = [None if a in defaults else a
            for a, defaults in zip(args, PATH_DEFAULTS)]
    user_id = args[0] if args else None
    paging_params = self.get_paging_params()

    # extract format
    expected_formats = ('json', 'atom', 'xml', 'html')
    format = self.request.get('format', 'json')
    if format not in expected_formats:
      raise exc.HTTPBadRequest('Invalid format: %s, expected one of %r' %
                               (format, expected_formats))

    # get activities and build response
    total_results, activities = source.get_activities(*args, **paging_params)

    response = {'startIndex': paging_params['start_index'],
                'itemsPerPage': len(activities),
                'totalResults': total_results,
                # TODO: this is just for compatibility with
                # http://activitystreamstester.appspot.com/
                # the OpenSocial spec says to use entry instead, so switch back
                # to that eventually
                'items': activities,
                'filtered': False,
                'sorted': False,
                'updatedSince': False,
                }

    if format == 'atom':
      # strip the access token from the request URL before returning
      params = dict(self.request.GET.items())
      for key in 'access_token', 'access_token_key', 'access_token_secret':
        if key in params:
          del params[key]
      request_url = '%s?%s' % (self.request.path_url, urllib.urlencode(params))
      actor = source.get_actor(user_id)
      response.update({
          'host_url': self.request.host_url + "/",
          'request_url': request_url,
          'title': 'User feed for ' + actor.get('displayName', 'unknown'),
          'updated': activities[0]['object'].get('published') if activities else '',
          'actor': actor,
          })

    # encode and write response
    self.response.headers['Access-Control-Allow-Origin'] = '*'
    if format == 'json':
      self.response.headers['Content-Type'] = 'application/json'
      self.response.out.write(json.dumps(response, indent=2))
    elif format == 'atom':
      self.response.headers['Content-Type'] = 'text/xml'
      self.response.out.write(template.render(ATOM_TEMPLATE_FILE, response))
    elif format == 'xml':
      self.response.headers['Content-Type'] = 'text/xml'
      self.response.out.write(XML_TEMPLATE % util.to_xml(response))
    elif format == 'html':
      self.response.headers['Content-Type'] = 'text/html'
      self.response.out.write('\n'.join(
          microformats2.object_to_html(a['object'], source_name=source.DOMAIN)
          for a in activities))

    if 'plaintext' in self.request.params:
      # override response content type
      self.response.headers['Content-Type'] = 'text/plain'

  def get_paging_params(self):
    """Extracts, normalizes and returns the startIndex and count query params.

    Returns:
      dict with 'start_index' and 'count' keys mapped to integers
    """
    start_index = self.get_positive_int('startIndex')
    count = self.get_positive_int('count')

    if count == 0:
      count = ITEMS_PER_PAGE - start_index
    else:
      count = min(count, ITEMS_PER_PAGE)

    return {'start_index': start_index, 'count': count}

  def get_positive_int(self, param):
    try:
      val = self.request.get(param, 0)
      val = int(val)
      assert val >= 0
      return val
    except (ValueError, AssertionError):
      raise exc.HTTPBadRequest('Invalid %s: %s (should be positive int)' %
                               (param, val))


application = webapp2.WSGIApplication([('.*', Handler)],
                                      debug=appengine_config.DEBUG)
