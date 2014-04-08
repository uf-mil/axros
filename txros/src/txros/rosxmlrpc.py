from __future__ import division

from twisted.internet import defer

from txros import util


class Error(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
    def __str__(self):
        return 'rosxmlrpc.Error' + repr((self.code, self.message))

class Proxy(object):
    def __init__(self, proxy, caller_id):
        self._proxy = proxy
        self._caller_id = caller_id
    
    def __getattr__(self, name):
        @util.inlineCallbacks
        def _(*args):
            statusCode, statusMessage, value = yield self._proxy.callRemote(name, self._caller_id, *args)
            if statusCode == 1: # SUCCESS
                defer.returnValue(value)
            else:
                raise Error(statusCode, statusMessage)
        return _