# -*- test-case-name: twisted.web.test.test_websocket -*-
# Copyright (c) 2009 Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Note: This is from the associated branch for http://twistedmatrix.com/trac/ticket/4173
and includes support for the hixie-76 handshake.

WebSocket server protocol.

See U{http://tools.ietf.org/html/draft-hixie-thewebsocketprotocol} for the
current version of the specification.

@since: 10.1
"""

from hashlib import md5
import struct

from twisted.internet import interfaces
from twisted.python import log
from twisted.web._newclient import makeStatefulDispatcher
from twisted.web.http import datetimeToString
from twisted.web.http import _IdentityTransferDecoder
from twisted.web.server import Request, Site, version, unquote
from zope.interface import implements


_ascii_numbers = frozenset(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'])

class WebSocketRequest(Request):
    """
    A general purpose L{Request} supporting connection upgrade for WebSocket.
    """

    def process(self):
        if (self.requestHeaders.getRawHeaders("Upgrade") == ["WebSocket"] and
            self.requestHeaders.getRawHeaders("Connection") == ["Upgrade"]):
            return self.processWebSocket()
        else:
            return Request.process(self)

    def processWebSocket(self):
        """
        Process a specific web socket request.
        """
        # get site from channel
        self.site = self.channel.site

        # set various default headers
        self.setHeader("server", version)
        self.setHeader("date", datetimeToString())

        # Resource Identification
        self.prepath = []
        self.postpath = map(unquote, self.path[1:].split("/"))
        self.renderWebSocket()


    def _clientHandshake76(self):
        """
        Complete hixie-76 handshake, which consists of a challenge and response.

        If the request is not identified with a proper WebSocket handshake, the
        connection will be closed. Otherwise, the response to the handshake is
        sent and a C{WebSocketHandler} is created to handle the request.
        """
        def finish():
            self.channel.transport.loseConnection()
        if self.queued:
            return finish()

        secKey1 = self.requestHeaders.getRawHeaders("Sec-WebSocket-Key1", [])
        secKey2 = self.requestHeaders.getRawHeaders("Sec-WebSocket-Key2", [])

        if len(secKey1) != 1 or len(secKey2) != 1:
            return finish()

        # copied
        originHeaders = self.requestHeaders.getRawHeaders("Origin", [])
        if len(originHeaders) != 1:
            return finish()
        hostHeaders = self.requestHeaders.getRawHeaders("Host", [])
        if len(hostHeaders) != 1:
            return finish()
        handlerFactory = self.site.handlers.get(self.uri)
        if not handlerFactory:
            return finish()

        # key1 and key2 exist and are a string of characters
        # filter both keys to get a string with all numbers in order
        key1 = secKey1[0]
        key2 = secKey2[0]
        numBuffer1 = ''.join([x for x in key1 if x in _ascii_numbers])
        numBuffer2 = ''.join([x for x in key2 if x in _ascii_numbers])

        # make sure numbers actually exist
        if not numBuffer1 or not numBuffer2:
            return finish()

        # these should be int-like
        num1 = int(numBuffer1)
        num2 = int(numBuffer2)

        # count the number of spaces in each character string
        numSpaces1 = 0
        for x in key1:
            if x == ' ':
                numSpaces1 += 1
        numSpaces2 = 0
        for x in key2:
            if x == ' ':
                numSpaces2 += 1

        # there should be at least one space in each
        if numSpaces1 == 0 or numSpaces2 == 0:
            return finish()

        # get two resulting numbers, as specified in hixie-76
        num1 = num1 / numSpaces1
        num2 = num2 / numSpaces2

        transport = WebSocketTransport(self)
        handler = handlerFactory(transport)
        transport._attachHandler(handler)

        self.channel.setRawMode()

        def finishHandshake(nonce):
            """ Receive nonce value from request body, and calculate repsonse. """
            protocolHeaders = self.requestHeaders.getRawHeaders(
                "WebSocket-Protocol", [])
            if len(protocolHeaders) not in (0,  1):
                return finish()
            if protocolHeaders:
                if protocolHeaders[0] not in self.site.supportedProtocols:
                    return finish()
                protocolHeader = protocolHeaders[0]
            else:
                protocolHeader = None

            originHeader = originHeaders[0]
            hostHeader = hostHeaders[0]
            self.startedWriting = True
            handshake = [
                "HTTP/1.1 101 Web Socket Protocol Handshake",
                "Upgrade: WebSocket",
                "Connection: Upgrade"]
            handshake.append("Sec-WebSocket-Origin: %s" % (originHeader))
            if self.isSecure():
                scheme = "wss"
            else:
                scheme = "ws"
            handshake.append(
                "Sec-WebSocket-Location: %s://%s%s" % (
                scheme, hostHeader, self.uri))

            if protocolHeader is not None:
                handshake.append("Sec-WebSocket-Protocol: %s" % protocolHeader)

            for header in handshake:
                self.write("%s\r\n" % header)

            self.write("\r\n")

            # concatenate num1 (32 bit in), num2 (32 bit int), nonce, and take md5 of result
            res = struct.pack('>II8s', num1, num2, nonce)
            server_response = md5(res).digest()
            self.write(server_response)

            # XXX we probably don't want to set _transferDecoder
            self.channel._transferDecoder = WebSocketFrameDecoder(
                self, handler)

            transport._connectionMade()

        # we need the nonce from the request body
        self.channel._transferDecoder = _IdentityTransferDecoder(0, lambda _ : None, finishHandshake)


    def _checkClientHandshake(self):
        """
        Verify client handshake, closing the connection in case of problem.

        @return: C{None} if a problem was detected, or a tuple of I{Origin}
            header, I{Host} header, I{WebSocket-Protocol} header, and
            C{WebSocketHandler} instance. The I{WebSocket-Protocol} header will
            be C{None} if not specified by the client.
        """
        def finish():
            self.channel.transport.loseConnection()
        if self.queued:
            return finish()
        originHeaders = self.requestHeaders.getRawHeaders("Origin", [])
        if len(originHeaders) != 1:
            return finish()
        hostHeaders = self.requestHeaders.getRawHeaders("Host", [])
        if len(hostHeaders) != 1:
            return finish()

        handlerFactory = self.site.handlers.get(self.uri)
        if not handlerFactory:
            return finish()
        transport = WebSocketTransport(self)
        handler = handlerFactory(transport)
        transport._attachHandler(handler)

        protocolHeaders = self.requestHeaders.getRawHeaders(
            "WebSocket-Protocol", [])
        if len(protocolHeaders) not in (0,  1):
            return finish()
        if protocolHeaders:
            if protocolHeaders[0] not in self.site.supportedProtocols:
                return finish()
            protocolHeader = protocolHeaders[0]
        else:
            protocolHeader = None
        return originHeaders[0], hostHeaders[0], protocolHeader, handler


    def renderWebSocket(self):
        """
        Render a WebSocket request.

        If the request is not identified with a proper WebSocket handshake, the
        connection will be closed. Otherwise, the response to the handshake is
        sent and a C{WebSocketHandler} is created to handle the request.
        """
        # check for post-75 handshake requests
        isSecHandshake = self.requestHeaders.getRawHeaders("Sec-WebSocket-Key1", [])
        if isSecHandshake:
            self._clientHandshake76()
        else:
            check = self._checkClientHandshake()
            if check is None:
                return
            originHeader, hostHeader, protocolHeader, handler = check
            self.startedWriting = True
            handshake = [
                "HTTP/1.1 101 Web Socket Protocol Handshake",
                "Upgrade: WebSocket",
                "Connection: Upgrade"]
            handshake.append("WebSocket-Origin: %s" % (originHeader))
            if self.isSecure():
                scheme = "wss"
            else:
                scheme = "ws"
            handshake.append(
                "WebSocket-Location: %s://%s%s" % (
                scheme, hostHeader, self.uri))

            if protocolHeader is not None:
                handshake.append("WebSocket-Protocol: %s" % protocolHeader)

            for header in handshake:
                self.write("%s\r\n" % header)

            self.write("\r\n")
            self.channel.setRawMode()
            # XXX we probably don't want to set _transferDecoder
            self.channel._transferDecoder = WebSocketFrameDecoder(
                self, handler)
            handler.transport._connectionMade()
            return



class WebSocketSite(Site):
    """
    @ivar handlers: a C{dict} of names to L{WebSocketHandler} factories.
    @type handlers: C{dict}
    @ivar supportedProtocols: a C{list} of supported I{WebSocket-Protocol}
        values. If a value is passed at handshake and doesn't figure in this
        list, the connection is closed.
    @type supportedProtocols: C{list}
    """
    requestFactory = WebSocketRequest

    def __init__(self, resource, logPath=None, timeout=60*60*12,
                 supportedProtocols=None):
        Site.__init__(self, resource, logPath, timeout)
        self.handlers = {}
        self.supportedProtocols = supportedProtocols or []

    def addHandler(self, name, handlerFactory):
        """
        Add or override a handler for the given C{name}.

        @param name: the resource name to be handled.
        @type name: C{str}
        @param handlerFactory: a C{WebSocketHandler} factory.
        @type handlerFactory: C{callable}
        """
        if not name.startswith("/"):
            raise ValueError("Invalid resource name.")
        self.handlers[name] = handlerFactory



class WebSocketTransport(object):
    """
    Transport abstraction over WebSocket, providing classic Twisted methods and
    callbacks.
    """
    implements(interfaces.ITransport)

    _handler = None

    def __init__(self, request):
        self._request = request
        self._request.notifyFinish().addErrback(self._connectionLost)

    def _attachHandler(self, handler):
        """
        Attach the given L{WebSocketHandler} to this transport.
        """
        self._handler = handler

    def _connectionMade(self):
        """
        Called when a connection is made.
        """
        self._handler.connectionMade()

    def _connectionLost(self, reason):
        """
        Forward connection lost event to the L{WebSocketHandler}.
        """
        self._handler.connectionLost(reason)
        del self._request.transport
        del self._request
        del self._handler

    def getPeer(self):
        """
        Return a tuple describing the other side of the connection.

        @rtype: C{tuple}
        """
        return self._request.transport.getPeer()

    def getHost(self):
        """
        Similar to getPeer, but returns an address describing this side of the
        connection.

        @return: An L{IAddress} provider.
        """

        return self._request.transport.getHost()

    def write(self, frame):
        """
        Send the given frame to the connected client.

        @param frame: a I{UTF-8} encoded C{str} to send to the client.
        @type frame: C{str}
        """
        self._request.write("\x00%s\xff" % frame)

    def writeSequence(self, frames):
        """
        Send a sequence of frames to the connected client.
        """
        self._request.write("".join(["\x00%s\xff" % f for f in frames]))

    def loseConnection(self):
        """
        Close the connection.
        """
        self._request.transport.loseConnection()
        del self._request.transport
        del self._request
        del self._handler

class WebSocketHandler(object):
    """
    Base class for handling WebSocket connections. It mainly provides a
    transport to send frames, and a callback called when frame are received,
    C{frameReceived}.

    @ivar transport: a C{WebSocketTransport} instance.
    @type: L{WebSocketTransport}
    """

    def __init__(self, transport):
        """
        Create the handler, with the given transport
        """
        self.transport = transport


    def frameReceived(self, frame):
        """
        Called when a frame is received.

        @param frame: a I{UTF-8} encoded C{str} sent by the client.
        @type frame: C{str}
        """


    def frameLengthExceeded(self):
        """
        Called when too big a frame is received. The default behavior is to
        close the connection, but it can be customized to do something else.
        """
        self.transport.loseConnection()


    def connectionMade(self):
        """
        Called when a connection is made.
        """

    def connectionLost(self, reason):
        """
        Callback called when the underlying transport has detected that the
        connection is closed.
        """


class IncompleteFrame(Exception):
    """
    Not enough data to complete a WebSocket frame.
    """


class DecodingError(Exception):
    """
    The incoming data is not valid WebSocket protocol data.
    """


class WebSocketFrameDecoder(object):
    """
    Decode WebSocket frames and pass them to the attached C{WebSocketHandler}
    instance.

    @ivar MAX_LENGTH: maximum len of a text frame allowed, before calling
        C{frameLengthExceeded} on the handler.
    @type MAX_LENGTH: C{int}
    @ivar MAX_BINARY_LENGTH: like C{MAX_LENGTH}, but for 0xff type frames
    @type MAX_BINARY_LENGTH: C{int}
    @ivar closing: a flag set when the closing handshake has been received
    @type closing: C{bool}
    @ivar request: C{Request} instance.
    @type request: L{twisted.web.server.Request}
    @ivar handler: L{WebSocketHandler} instance handling the request.
    @type handler: L{WebSocketHandler}
    @ivar _data: C{list} of C{str} buffering the received data.
    @type _data: C{list} of C{str}
    @ivar _currentFrameLength: length of the current handled frame, plus the
        additional leading byte.
    @type _currentFrameLength: C{int}
    """

    MAX_LENGTH = 16384
    MAX_BINARY_LENGTH = 2147483648
    closing = False

    def __init__(self, request, handler):
        self.request = request
        self.handler = handler
        self.closing = False
        self._data = []
        self._currentFrameLength = 0
        self._state = "FRAME_START"

    def dataReceived(self, data):
        """
        Parse data to read WebSocket frames.

        @param data: data received over the WebSocket connection.
        @type data: C{str}
        """
        if not data or self.closing:
            return
        self._data.append(data)

        while self._data and not self.closing:
            try:
                self.consumeData(self._data[-1])
            except IncompleteFrame:
                break
            except DecodingError:
                log.err()
                self.request.transport.loseConnection()
                break

    def consumeData(self, data):
        """
        Process the last data chunk received.

        After processing is done, L{IncompleteFrame} should be raised or
        L{_addRemainingData} should be called.

        @param data: last chunk of data received.
        @type data: C{str}
        """
    consumeData = makeStatefulDispatcher("consumeData", consumeData)

    def _consumeData_FRAME_START(self, data):
        self._currentFrameLength = 0

        if data[0] == "\x00":
            self._state = "PARSING_TEXT_FRAME"
        elif data[0] == "\xff":
            self._state = "PARSING_LENGTH"
        else:
            raise DecodingError("Invalid frame type 0x%s" %
                                data[0].encode("hex"))

        self._addRemainingData(data[1:])

    def _consumeData_PARSING_TEXT_FRAME(self, data):
        endIndex = data.find("\xff")
        if endIndex == -1:
            self._currentFrameLength += len(data)
        else:
            self._currentFrameLength += endIndex

        self._currentFrameLength += endIndex
        # check length + 1 to account for the initial frame type byte
        if self._currentFrameLength + 1 > self.MAX_LENGTH:
            self.handler.frameLengthExceeded()

        if endIndex == -1:
            raise IncompleteFrame()

        frame = "".join(self._data[:-1]) + data[:endIndex]
        self.handler.frameReceived(frame)

        remainingData = data[endIndex + 1:]
        self._addRemainingData(remainingData)

        self._state = "FRAME_START"

    def _consumeData_PARSING_LENGTH(self, data):
        current = 0
        available = len(data)

        while current < available:
            byte = ord(data[current])
            length, more = byte & 0x7F, bool(byte & 0x80)

            if not length:
                self._closingHandshake()
                raise IncompleteFrame()

            self._currentFrameLength *= 128
            self._currentFrameLength += length

            current += 1

            if not more:
                if self._currentFrameLength > self.MAX_BINARY_LENGTH:
                    self.handler.frameLengthExceeded()

                remainingData = data[current:]
                self._addRemainingData(remainingData)
                self._state = "PARSING_BINARY_FRAME"
                break
        else:
            raise IncompleteFrame()

    def _consumeData_PARSING_BINARY_FRAME(self, data):
        available = len(data)

        if self._currentFrameLength <= available:
            remainingData = data[self._currentFrameLength:]
            self._addRemainingData(remainingData)
            self._state = "FRAME_START"
        else:
            self._currentFrameLength -= available
            self._data[:] = []

    def _addRemainingData(self, remainingData):
        if remainingData:
            self._data[:] = [remainingData]
        else:
            self._data[:] = []

    def _closingHandshake(self):
        self.closing = True
        # send the closing handshake
        self.request.transport.write("\xff\x00")
        # discard all buffered data
        self._data[:] = []


__all__ = ["WebSocketHandler", "WebSocketSite"]
