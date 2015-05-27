#!/usr/bin/env python2
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)
from future_builtins import map

__license__   = 'GPL v3'
__copyright__ = '2015, Andrew Braini <ibgoge@gmail.com>'
__docformat__ = 'restructuredtext en'

import json
from functools import wraps
from binascii import hexlify, unhexlify
from io import BytesIO
from threading import Event
import re

import cherrypy

from calibre.ebooks.metadata.sources.base import create_log

from calibre import prints
from calibre.utils.date import isoformat
from calibre.utils.config import prefs, tweaks
from calibre.ebooks.metadata import title_sort
from calibre.ebooks.metadata import MetaInformation
from calibre.ebooks.metadata.book.json_codec import JsonCodec
from calibre.utils.icu import sort_key
from calibre.library.server import custom_fields_to_display
from calibre import force_unicode, isbytestring
from calibre.library.field_metadata import category_icon_map
from calibre.ptempfile import (PersistentTemporaryDirectory,
        PersistentTemporaryFile)
from calibre.ebooks.metadata.sources.identify import identify
from calibre.ebooks.metadata.sources.covers import download_cover

class Endpoint(object):  # {{{

    'Manage mime-type json serialization, etc.'

    def __init__(self, mimetype='application/json; charset=utf-8',
            set_last_modified=True):
        self.mimetype = mimetype
        self.set_last_modified = set_last_modified

    def __call__(eself, func):

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # Remove AJAX caching disabling jquery workaround arg
            # This arg is put into AJAX queries by jQuery to prevent
            # caching in the browser. We dont want it passed to the wrapped
            # function
            kwargs.pop('_', None)

            ans = func(self, *args, **kwargs)
#            cherrypy.response.headers['Content-Type'] = eself.mimetype
            if eself.set_last_modified:
                updated = self.db.last_modified()
                cherrypy.response.headers['Last-Modified'] = \
                    self.last_modified(max(updated, self.build_time))
#            if 'application/json' in eself.mimetype:
#                ans = json.dumps(ans, indent=2,
#                        ensure_ascii=False).encode('utf-8')
            return ans

        return wrapper
# }}}

# URL Encoding {{{
def encode_name(name):
    if isinstance(name, unicode):
        name = name.encode('utf-8')
    return hexlify(name)

def decode_name(name):
    return unhexlify(name).decode('utf-8')

def absurl(prefix, url):
    return prefix + url

def category_url(prefix, cid):
    return absurl(prefix, '/ajax/category/'+encode_name(cid))

def icon_url(prefix, name):
    return absurl(prefix, '/browse/icon/'+name)

def books_in_url(prefix, category, cid):
    return absurl(prefix, '/ajax/books_in/%s/%s'%(
        encode_name(category), encode_name(cid)))
# }}}

class ISBNServer(object):

    def __init__(self):
        self.ajax_json_codec = JsonCodec()

    def add_routes(self, connect):
        base_href = '/isbn'
        
        # SPA App
        connect('isbn_app', base_href+'/', self.isbn_app,
                conditions=dict(method=['GET']))

        # API Get
        connect('isbn_api_get', base_href+'/api/isbn/{isbn}', self.isbn_api_get,
                conditions=dict(method=['GET']))

        # API Add
        connect('isbn_api_add', base_href+'/api/isbn', self.isbn_api_add,
                conditions=dict(method=['POST']))

    # Single Page App for adding books by isbn using a mobile device {{{
    @Endpoint()
    def isbn_app(self):
        '''
        Get the String Page App
        '''
        cherrypy.response.headers['Content-Type'] = 'text/html'
        return P('content_server/isbn/index.html', data=True).decode('utf-8')

    # }}}

    @Endpoint()
    def isbn_js_app_ctrl(self):
        '''
        Get the String Page App
        '''
        page = P('content_server/isbn/isbn.ctrl.js', data=True).decode('utf-8')
        return page

    # }}}

    # Get a book by ISBN  {{{
    @Endpoint()
    def isbn_api_get(self, isbn):
        '''
        Add the book and return the new db id.
        '''

        try:
            isbn = int(re.sub(r'[^\d]+', '', isbn))
        except:
            raise cherrypy.HTTPError(404, 'Invalid isbn %s is not a number: '%isbn)
        isbn_len = len(str(isbn))
        if isbn_len != 10 and isbn_len != 13:
            raise cherrypy.HTTPError(404, 'Invalid isbn {0} has a wrong length of {1}'.format(isbn, isbn_len))

        return 'Getting metadata for isbn {0}'.format(isbn)

    # }}}

    # Add a book by ISBN  {{{
    @Endpoint()
    def isbn_api_add(self, isbn):
        '''
        Add the book and return the new db id.
        '''

        try:
            isbn = int(re.sub(r'[^\d]+', '', isbn))
        except:
            raise cherrypy.HTTPError(404, 'Invalid isbn %s is not a number: '%isbn)
        isbn_len = len(str(isbn))
        if isbn_len != 10 and isbn_len != 13:
            raise cherrypy.HTTPError(404, 'Invalid isbn {0} has a wrong length of {1}'.format(isbn, isbn_len))
        mi = MetaInformation(None)
        mi.isbn = str(isbn)
        fmts = []
        new_id = 0
        try:
            new_id = self.db.import_book(mi, fmts)
        except:
            return 'could not add new book with isdb {0}'.format(isbn)

        # Start the threaded download of metadata and return with the id of the added book
        result = ''
        try:
            ids = []
            ids.append(new_id)

            buf = BytesIO()
            log = create_log(buf)
            abort = Event()

            authors = []
            identifiers = {}
            identifiers['isbn'] = mi.isbn

            results = identify(log, abort, title=None, authors=authors,
                    identifiers=identifiers, timeout=int(30000))

            if not results:
                print (log, file=sys.stderr)
                prints('No results found', file=sys.stderr)
                return 'Could not find metadata for isbn {0}'.format(isbn)
            result = results[0]
            self.db.set_metadata(new_id, result)

            #cf = None
            #
            #if opts.cover and results:
            #    cover = download_cover(log, title=None, authors=authors,
            #            identifiers=result.identifiers, timeout=int(30000))
            #    if cover is None and not opts.opf:
            #        prints('No cover found', file=sys.stderr)
            #    else:
            #        save_cover_data_to(cover[-1], opts.cover)
            #        result.cover = cf = opts.cover

            log = buf.getvalue()

            result = unicode(result).encode('utf-8')

        except e:
            return 'Error getting metadata {0}'.format(e)

        return 'Added new book with isbn {0} with new id {1} and metadata {2}'.format(isbn, new_id, result)

    # }}}