# Copyright (c) 2009 Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Tests for L{twisted.web.websocket}.
"""
import base64
from hashlib import sha1

from twisted.internet.main import CONNECTION_DONE
from twisted.internet.error import ConnectionDone
from twisted.python.failure import Failure

from websocket import WebSocketHandler, WebSocketFrameDecoder
from websocket import WebSocketHybiFrameDecoder
from websocket import WebSocketSite, WebSocketTransport, WebSocketHybiTransport
from websocket import DecodingError, OPCODE_PING, OPCODE_TEXT

from twisted.web.resource import Resource
from twisted.web.server import Request, Site
from twisted.web.test.test_web import DummyChannel
from twisted.trial.unittest import TestCase



class DummyChannel(DummyChannel):
    """
    A L{DummyChannel} supporting the C{setRawMode} method.

    @ivar raw: C{bool} indicating if C{setRawMode} has been called.
    """

    raw = False

    def setRawMode(self):
        self.raw = True



class TestHandler(WebSocketHandler):
    """
    A L{WebSocketHandler} recording every frame received.

    @ivar frames: C{list} of frames received.
    @ivar lostReason: reason for connection closing.
    """

    def __init__(self, request):
        WebSocketHandler.__init__(self, request)
        self.frames = []
        self.binaryFrames = []
        self.pongs = []
        self.closes = []
        self.lostReason = None


    def frameReceived(self, frame):
        self.frames.append(frame)


    def binaryFrameReceived(self, frame):
        self.binaryFrames.append(frame)


    def pongReceived(self, data):
        self.pongs.append(data)


    def closeReceived(self, code, msg):
        self.closes.append((code, msg))


    def connectionLost(self, reason):
        self.lostReason = reason



class WebSocketSiteTestCase(TestCase):
    """
    Tests for L{WebSocketSite}.
    """

    def setUp(self):
        self.site = WebSocketSite(Resource())
        self.site.addHandler("/test", TestHandler)


    def renderRequest(self, headers=None, url="/test", ssl=False,
                      queued=False, body=None):
        """
        Render a request against C{self.site}, writing the WebSocket
        handshake.
        """
        if headers is None:
            headers = [
                ("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
                ("Host", "localhost"), ("Origin", "http://localhost/")]
        channel = DummyChannel()
        if ssl:
            channel.transport = channel.SSL()
        channel.site = self.site
        request = self.site.requestFactory(channel, queued)
        # store the reference to the request, so the tests can access it
        channel.request = request
        for k, v in headers:
            request.requestHeaders.addRawHeader(k, v)
        request.gotLength(0)
        request.requestReceived("GET", url, "HTTP/1.1")
        if body:
            request.channel._transferDecoder.finishCallback(body)
        return channel


    def test_multiplePostpath(self):
        """
        A resource name can consist of several path elements.
        """
        handlers = []
        def handlerFactory(request):
            handler = TestHandler(request)
            handlers.append(handler)
            return handler
        self.site.addHandler("/foo/bar", handlerFactory)
        channel = self.renderRequest(url="/foo/bar")
        self.assertEquals(len(handlers), 1)
        self.assertFalse(channel.transport.disconnected)


    def test_queryArguments(self):
        """
        A resource name may contain query arguments.
        """
        handlers = []
        def handlerFactory(request):
            handler = TestHandler(request)
            handlers.append(handler)
            return handler
        self.site.addHandler("/test?foo=bar&egg=spam", handlerFactory)
        channel = self.renderRequest(url="/test?foo=bar&egg=spam")
        self.assertEquals(len(handlers), 1)
        self.assertFalse(channel.transport.disconnected)


    def test_noOriginHeader(self):
        """
        If no I{Origin} header is present, the connection is closed.
        """
        channel = self.renderRequest(
            headers=[("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
                     ("Host", "localhost")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_multipleOriginHeaders(self):
        """
        If more than one I{Origin} header is present, the connection is
        dropped.
        """
        channel = self.renderRequest(
            headers=[("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
                     ("Host", "localhost"), ("Origin", "foo"),
                     ("Origin", "bar")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_noHostHeader(self):
        """
        If no I{Host} header is present, the connection is dropped.
        """
        channel = self.renderRequest(
            headers=[("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
                     ("Origin", "http://localhost/")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_multipleHostHeaders(self):
        """
        If more than one I{Host} header is present, the connection is
        dropped.
        """
        channel = self.renderRequest(
            headers=[("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
                     ("Origin", "http://localhost/"), ("Host", "foo"),
                     ("Host", "bar")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_missingHandler(self):
        """
        If no handler is registered for the given resource, the connection is
        dropped.
        """
        channel = self.renderRequest(url="/foo")
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_noConnectionUpgrade(self):
        """
        If the I{Connection: Upgrade} header is not present, the connection is
        dropped.
        """
        channel = self.renderRequest(
            headers=[("Upgrade", "WebSocket"), ("Host", "localhost"),
                     ("Origin", "http://localhost/")])
        self.assertIn("404 Not Found", channel.transport.written.getvalue())


    def test_noUpgradeWebSocket(self):
        """
        If the I{Upgrade: WebSocket} header is not present, the connection is
        dropped.
        """
        channel = self.renderRequest(
            headers=[("Connection", "Upgrade"), ("Host", "localhost"),
                     ("Origin", "http://localhost/")])
        self.assertIn("404 Not Found", channel.transport.written.getvalue())


    def test_render(self):
        """
        If the handshake is successful, we can read back the server handshake,
        and the channel is setup for raw mode.
        """
        channel = self.renderRequest()
        self.assertTrue(channel.raw)
        self.assertEquals(
            channel.transport.written.getvalue(),
            "HTTP/1.1 101 Web Socket Protocol Handshake\r\n"
            "Upgrade: WebSocket\r\n"
            "Connection: Upgrade\r\n"
            "WebSocket-Origin: http://localhost/\r\n"
            "WebSocket-Location: ws://localhost/test\r\n\r\n")
        self.assertFalse(channel.transport.disconnected)

    def test_render_handShake76(self):
        """
        Test a hixie-76 handShake.
        """
        # we need to construct a challenge
        key1 = '1x0x0 0y00 0'  # 1000000
        key2 = '1b0b0 000 0'   # 1000000
        body = '12345678'
        headers = [
            ("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("Sec-WebSocket-Key1", key1), ("Sec-WebSocket-Key2", key2)]
        channel = self.renderRequest(headers=headers, body=body)

        self.assertTrue(channel.raw)

        result = channel.transport.written.getvalue()

        headers, response = result.split('\r\n\r\n')

        self.assertEquals(
            headers,
            "HTTP/1.1 101 Web Socket Protocol Handshake\r\n"
            "Upgrade: WebSocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Origin: http://localhost/\r\n"
            "Sec-WebSocket-Location: ws://localhost/test")

        # check challenge is correct
        from hashlib import md5
        import struct
        self.assertEquals(md5(struct.pack('>ii8s', 500000, 500000, body)).digest(), response)

        self.assertFalse(channel.transport.disconnected)

    def test_secureRender(self):
        """
        If the WebSocket connection is over SSL, the I{WebSocket-Location}
        header specified I{wss} as scheme.
        """
        channel = self.renderRequest(ssl=True)
        self.assertTrue(channel.raw)
        self.assertEquals(
            channel.transport.written.getvalue(),
            "HTTP/1.1 101 Web Socket Protocol Handshake\r\n"
            "Upgrade: WebSocket\r\n"
            "Connection: Upgrade\r\n"
            "WebSocket-Origin: http://localhost/\r\n"
            "WebSocket-Location: wss://localhost/test\r\n\r\n")
        self.assertFalse(channel.transport.disconnected)


    def test_frameReceived(self):
        """
        C{frameReceived} is called with the received frames after handshake.
        """
        handlers = []
        def handlerFactory(request):
            handler = TestHandler(request)
            handlers.append(handler)
            return handler
        self.site.addHandler("/test2", handlerFactory)
        channel = self.renderRequest(url="/test2")
        self.assertEquals(len(handlers), 1)
        handler = handlers[0]
        channel._transferDecoder.dataReceived("\x00hello\xff\x00boy\xff")
        self.assertEquals(handler.frames, ["hello", "boy"])


    def test_websocketProtocolAccepted(self):
        """
        The I{WebSocket-Protocol} header is echoed by the server if the
        protocol is among the supported protocols.
        """
        self.site.supportedProtocols.append("pixiedust")
        channel = self.renderRequest(
            headers = [
            ("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("WebSocket-Protocol", "pixiedust")])
        self.assertTrue(channel.raw)
        self.assertEquals(
            channel.transport.written.getvalue(),
            "HTTP/1.1 101 Web Socket Protocol Handshake\r\n"
            "Upgrade: WebSocket\r\n"
            "Connection: Upgrade\r\n"
            "WebSocket-Origin: http://localhost/\r\n"
            "WebSocket-Location: ws://localhost/test\r\n"
            "WebSocket-Protocol: pixiedust\r\n\r\n")
        self.assertFalse(channel.transport.disconnected)


    def test_tooManyWebSocketProtocol(self):
        """
        If more than one I{WebSocket-Protocol} headers are specified, the
        connection is dropped.
        """
        self.site.supportedProtocols.append("pixiedust")
        channel = self.renderRequest(
            headers = [
            ("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("WebSocket-Protocol", "pixiedust"),
            ("WebSocket-Protocol", "fairymagic")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_unsupportedProtocols(self):
        """
        If the I{WebSocket-Protocol} header specified an unsupported protocol,
        the connection is dropped.
        """
        self.site.supportedProtocols.append("pixiedust")
        channel = self.renderRequest(
            headers = [
            ("Upgrade", "WebSocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("WebSocket-Protocol", "fairymagic")])
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_queued(self):
        """
        Queued requests are unsupported, thus closed by the
        C{WebSocketSite}.
        """
        channel = self.renderRequest(queued=True)
        self.assertFalse(channel.transport.written.getvalue())
        self.assertTrue(channel.transport.disconnected)


    def test_addHandlerWithoutSlash(self):
        """
        C{addHandler} raises C{ValueError} if the resource name doesn't start
        with a slash.
        """
        self.assertRaises(
            ValueError, self.site.addHandler, "test", TestHandler)


    def test_render_handShakeHybi(self):
        """
        Test a hybi-10 handshake.
        """
        # the key is a base64 encoded 16-bit integer, here chosen to be 14
        key = "AA4="
        headers = [
            ("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("Sec-WebSocket-Version", "8"), ("Sec-WebSocket-Key", key)]
        channel = self.renderRequest(headers=headers)

        self.assertTrue(channel.raw)

        result = channel.transport.written.getvalue()
        headers, response = result.split('\r\n\r\n')

        guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept = base64.b64encode(sha1(key + guid).digest())
        self.assertEquals(
            headers,
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s" % accept)

        self.assertFalse(channel.transport.disconnected)
        self.assertFalse(channel.request.finished)


    def test_hybiWrongVersion(self):
        """
        A handshake that requests an unsupported version of the protocol
        results in HTTP 426.
        """
        key = "AA4="
        headers = [
            ("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("Sec-WebSocket-Version", "9"), ("Sec-WebSocket-Key", key)]
        channel = self.renderRequest(headers=headers)

        result = channel.transport.written.getvalue()

        self.assertIn("HTTP/1.1 426", result)
        # Twisted canonicalizes header names (see
        # http_headers.Headers._canonicalNameCaps), so it's not
        # Sec-WebSocket-Version, but Sec-Websocket-Version, but clients
        # understand it anyway
        self.assertIn("Sec-Websocket-Version: 8", result)
        self.assertTrue(channel.request.finished)


    def test_hybiNoKey(self):
        """
        A handshake without a websocket key results in HTTP 400.
        """
        headers = [
            ("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("Sec-WebSocket-Version", "8")]
        channel = self.renderRequest(headers=headers)

        result = channel.transport.written.getvalue()

        self.assertIn("HTTP/1.1 400", result)
        self.assertTrue(channel.request.finished)


    def test_hybiNotFound(self):
        """
        A request for an unknown endpoint results in HTTP 404.
        """
        key = "AA4="
        headers = [
            ("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Host", "localhost"), ("Origin", "http://localhost/"),
            ("Sec-WebSocket-Version", "8"), ("Sec-WebSocket-Key", key)]
        channel = self.renderRequest(headers=headers, url="/foo")

        result = channel.transport.written.getvalue()

        self.assertIn("HTTP/1.1 404", result)
        self.assertTrue(channel.request.finished)


class WebSocketFrameDecoderTestCase(TestCase):
    """
    Test for C{WebSocketFrameDecoder}.
    """

    def setUp(self):
        self.channel = DummyChannel()
        request = Request(self.channel, False)
        transport = WebSocketTransport(request)
        handler = TestHandler(transport)
        transport._attachHandler(handler)
        self.decoder = WebSocketFrameDecoder(request, handler)
        self.decoder.MAX_LENGTH = 100
        self.decoder.MAX_BINARY_LENGTH = 1000


    def assertOneDecodingError(self):
        """
        Assert that exactly one L{DecodingError} has been logged and return
        that error.
        """
        errors = self.flushLoggedErrors(DecodingError)
        self.assertEquals(len(errors), 1)
        return errors[0]


    def test_oneFrame(self):
        """
        We can send one frame handled with one C{dataReceived} call.
        """
        self.decoder.dataReceived("\x00frame\xff")
        self.assertEquals(self.decoder.handler.frames, ["frame"])


    def test_oneFrameSplitted(self):
        """
        A frame can be split into several C{dataReceived} calls, and will be
        combined again when sent to the C{WebSocketHandler}.
        """
        self.decoder.dataReceived("\x00fra")
        self.decoder.dataReceived("me\xff")
        self.assertEquals(self.decoder.handler.frames, ["frame"])


    def test_multipleFrames(self):
        """
        Several frames can be received in a single C{dataReceived} call.
        """
        self.decoder.dataReceived("\x00frame1\xff\x00frame2\xff")
        self.assertEquals(self.decoder.handler.frames, ["frame1", "frame2"])


    def test_missingNull(self):
        """
        If a frame not starting with C{\\x00} is received, the connection is
        dropped.
        """
        self.decoder.dataReceived("frame\xff")
        self.assertOneDecodingError()
        self.assertTrue(self.channel.transport.disconnected)


    def test_missingNullAfterGoodFrame(self):
        """
        If a frame not starting with C{\\x00} is received after a correct
        frame, the connection is dropped.
        """
        self.decoder.dataReceived("\x00frame\xfffoo")
        self.assertOneDecodingError()
        self.assertTrue(self.channel.transport.disconnected)
        self.assertEquals(self.decoder.handler.frames, ["frame"])


    def test_emptyReceive(self):
        """
        Received an empty string doesn't do anything.
        """
        self.decoder.dataReceived("")
        self.assertFalse(self.channel.transport.disconnected)


    def test_maxLength(self):
        """
        If a frame is received which is bigger than C{MAX_LENGTH}, the
        connection is dropped.
        """
        self.decoder.dataReceived("\x00" + "x" * 101)
        self.assertTrue(self.channel.transport.disconnected)


    def test_maxLengthFrameCompleted(self):
        """
        If a too big frame is received in several fragments, the connection is
        dropped.
        """
        self.decoder.dataReceived("\x00" + "x" * 90)
        self.decoder.dataReceived("x" * 11 + "\xff")
        self.assertTrue(self.channel.transport.disconnected)


    def test_frameLengthReset(self):
        """
        The length of frames is reset between frame, thus not creating an error
        when the accumulated length exceeds the maximum frame length.
        """
        for i in range(15):
            self.decoder.dataReceived("\x00" + "x" * 10 + "\xff")
        self.assertFalse(self.channel.transport.disconnected)


    def test_oneBinaryFrame(self):
        """
        A binary frame is parsed and ignored, the following text frame is
        delivered.
        """
        self.decoder.dataReceived("\xff\x0abinarydata\x00text frame\xff")
        self.assertEquals(self.decoder.handler.frames, ["text frame"])


    def test_multipleBinaryFrames(self):
        """
        Text frames intermingled with binary frames are parsed correctly.
        """
        tf1, tf2, tf3 = "\x00frame1\xff", "\x00frame2\xff", "\x00frame3\xff"
        bf1, bf2, bf3 = "\xff\x01X", "\xff\x1a" + "X" * 0x1a, "\xff\x02AB"

        self.decoder.dataReceived(tf1 + bf1 + bf2 + tf2 + tf3 + bf3)
        self.assertEquals(self.decoder.handler.frames,
                          ["frame1", "frame2", "frame3"])


    def test_binaryFrameMultipleLengthBytes(self):
        """
        A binary frame can have its length field spread across multiple bytes.
        """
        bf = "\xff\x81\x48" + "X" * 200
        tf = "\x00frame\xff"
        self.decoder.dataReceived(bf + tf + bf)
        self.assertEquals(self.decoder.handler.frames, ["frame"])


    def test_binaryAndTextSplitted(self):
        """
        Intermingled binary and text frames can be split across several
        C{dataReceived} calls.
        """
        tf1, tf2 = "\x00text\xff", "\x00other text\xff"
        bf1, bf2, bf3 = ("\xff\x01X", "\xff\x81\x48" + "X" * 200,
                         "\xff\x20" + "X" * 32)

        chunks = [bf1[0], bf1[1:], tf1[:2], tf1[2:] + bf2[:2], bf2[2:-2],
                  bf2[-2:-1], bf2[1] + tf2[:-1], tf2[-1], bf3]
        for c in chunks:
            self.decoder.dataReceived(c)

        self.assertEquals(self.decoder.handler.frames, ["text", "other text"])
        self.assertFalse(self.channel.transport.disconnected)


    def test_maxBinaryLength(self):
        """
        If a binary frame's declared length exceeds MAX_BINARY_LENGTH, the
        connection is dropped.
        """
        self.decoder.dataReceived("\xff\xff\xff\xff\xff\x01")
        self.assertTrue(self.channel.transport.disconnected)


    def test_closingHandshake(self):
        """
        After receiving the closing handshake, the server sends its own closing
        handshake and ignores all future data.
        """
        self.decoder.dataReceived("\x00frame\xff\xff\x00random crap")
        self.decoder.dataReceived("more random crap, that's discarded")
        self.assertEquals(self.decoder.handler.frames, ["frame"])
        self.assertTrue(self.decoder.closing)


    def test_invalidFrameType(self):
        """
        Frame types other than 0x00 and 0xff cause the connection to be
        dropped.
        """
        ok = "\x00ok\xff"
        wrong = "\x05foo\xff"

        self.decoder.dataReceived(ok + wrong + ok)
        self.assertEquals(self.decoder.handler.frames, ["ok"])
        error = self.assertOneDecodingError()
        self.assertTrue(self.channel.transport.disconnected)


    def test_emptyFrame(self):
        """
        An empty text frame is correctly parsed.
        """
        self.decoder.dataReceived("\x00\xff")
        self.assertEquals(self.decoder.handler.frames, [""])
        self.assertFalse(self.channel.transport.disconnected)


class WebSocketHybiFrameDecoderTestCase(TestCase):
    """
    Test for C{WebSocketHybiFrameDecoder}.
    """

    def setUp(self):
        self.channel = DummyChannel()
        request = Request(self.channel, False)
        transport = WebSocketHybiTransport(request)
        handler = TestHandler(transport)
        transport._attachHandler(handler)
        self.decoder = WebSocketHybiFrameDecoder(request, handler)
        self.decoder.MAX_LENGTH = 100
        self.decoder.MAX_BINARY_LENGTH = 1000
        # taken straight from the IETF draft, masking added where appropriate
        self.hello = "\x81\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58"
        self.frag_hello = ("\x01\x83\x12\x21\x65\x23\x5a\x44\x09",
                           "\x80\x82\x63\x34\xf1\x00\x0f\x5b")
        self.binary_orig = "\x3f" * 256
        self.binary = ("\x82\xfe\x01\x00\x12\x6d\xa6\x23" +
                       "\x2d\x52\x99\x1c" * 64)
        self.ping = "\x89\x85\x56\x23\x88\x23\x1e\x46\xe4\x4f\x39"
        self.pong = "\x8a\x85\xde\x41\x0f\x34\x96\x24\x63\x58\xb1"
        self.pong_unmasked = "\x8a\x05\x48\x65\x6c\x6c\x6f"
        # code 1000, message "Normal Closure"
        self.close = ("\x88\x90\x34\x23\x87\xde\x37\xcb\xc9\xb1\x46"
                      "\x4e\xe6\xb2\x14\x60\xeb\xb1\x47\x56\xf5\xbb")
        self.empty_unmasked_close = "\x88\x00"
        self.empty_text = "\x81\x80\x00\x01\x02\x03"
        self.cont_empty_text = "\x00\x80\x00\x01\x02\x03"


    def assertOneDecodingError(self):
        """
        Assert that exactly one L{DecodingError} has been logged and return
        that error.
        """
        errors = self.flushLoggedErrors(DecodingError)
        self.assertEquals(len(errors), 1)
        return errors[0]


    def test_oneTextFrame(self):
        """
        We can send one frame handled with one C{dataReceived} call.
        """
        self.decoder.dataReceived(self.hello)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])


    def test_chunkedTextFrame(self):
        """
        We can send one text frame handled with multiple C{dataReceived} calls.
        """
        # taken straight from the IETF draft
        for part in (self.hello[:1], self.hello[1:3],
                     self.hello[3:7], self.hello[7:]):
            self.decoder.dataReceived(part)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])


    def test_fragmentedTextFrame(self):
        """
        We can send a fragmented frame handled with one C{dataReceived} call.
        """
        self.decoder.dataReceived("".join(self.frag_hello))
        self.assertEquals(self.decoder.handler.frames, ["Hello"])


    def test_chunkedfragmentedTextFrame(self):
        """
        We can send a fragmented text frame handled with multiple
        C{dataReceived} calls.
        """
        # taken straight from the IETF draft
        for part in (self.frag_hello[0][:3], self.frag_hello[0][3:]):
            self.decoder.dataReceived(part)
        for part in (self.frag_hello[1][:1], self.frag_hello[1][1:]):
            self.decoder.dataReceived(part)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])


    def test_twoFrames(self):
        """
        We can send two frames together and they will be correctly parsed.
        """
        self.decoder.dataReceived("".join(self.frag_hello) + self.hello)
        self.assertEquals(self.decoder.handler.frames, ["Hello"] * 2)


    def test_controlInterleaved(self):
        """
        A control message (in this case a pong) can appear between the
        fragmented frames.
        """
        data = self.frag_hello[0] + self.pong + self.frag_hello[1]
        for part in data[:2], data[2:7], data[7:8], data[8:14], data[14:]:
            self.decoder.dataReceived(part)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])
        self.assertEquals(self.decoder.handler.pongs, ["Hello"])


    def test_binaryFrame(self):
        """
        We can send a binary frame that uses a longer length field.
        """
        data = self.binary
        for part in data[:3], data[3:4], data[4:]:
            self.decoder.dataReceived(part)
        self.assertEquals(self.decoder.handler.binaryFrames,
                          [self.binary_orig])


    def test_pingInterleaved(self):
        """
        We can get a ping frame in the middle of a fragmented frame and we'll
        correctly send a pong resonse.
        """
        data = self.frag_hello[0] + self.ping + self.frag_hello[1]
        for part in data[:12], data[12:16], data[16:]:
            self.decoder.dataReceived(part)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])

        result = self.channel.transport.written.getvalue()
        headers, response = result.split('\r\n\r\n')

        self.assertEquals(response, self.pong_unmasked)


    def test_close(self):
        """
        A close frame causes the remaining data to be discarded and the
        connection to be closed.
        """
        self.decoder.dataReceived(self.hello + self.close + "crap" * 20)
        self.assertEquals(self.decoder.handler.frames, ["Hello"])
        self.assertEquals(self.decoder.handler.closes,
                          [(1000, "Normal Closure")])

        result = self.channel.transport.written.getvalue()
        headers, response = result.split('\r\n\r\n')

        self.assertEquals(response, self.empty_unmasked_close)
        self.assertTrue(self.channel.transport.disconnected)


    def test_emptyFrame(self):
        """
        An empty text frame is correctly parsed.
        """
        self.decoder.dataReceived(self.empty_text)
        self.assertEquals(self.decoder.handler.frames, [""])


    def test_emptyFrameInterleaved(self):
        """
        An empty fragmented frame and a interleaved pong message are received
        and parsed.
        """
        data = (self.frag_hello[0] + self.cont_empty_text +
                self.pong + self.frag_hello[1])
        for part in data[:1], data[1:8], data[8:17], data[17:]:
            self.decoder.dataReceived(part)

        self.assertEquals(self.decoder.handler.frames, ["Hello"])
        self.assertEquals(self.decoder.handler.pongs, ["Hello"])


class WebSocketHandlerTestCase(TestCase):
    """
    Tests for L{WebSocketHandler}.
    """

    def setUp(self):
        self.channel = DummyChannel()
        self.request = request = Request(self.channel, False)
        # Simulate request handling
        request.startedWriting = True
        transport = WebSocketTransport(request)
        self.handler = TestHandler(transport)
        transport._attachHandler(self.handler)


    def test_write(self):
        """
        L{WebSocketTransport.write} adds the required C{\\x00} and C{\\xff}
        around sent frames, and write it to the request.
        """
        self.handler.transport.write("hello")
        self.handler.transport.write("world")
        self.assertEquals(
            self.channel.transport.written.getvalue(),
            "\x00hello\xff\x00world\xff")
        self.assertFalse(self.channel.transport.disconnected)


    def test_close(self):
        """
        L{WebSocketTransport.loseConnection} closes the underlying request.
        """
        self.handler.transport.loseConnection()
        self.assertTrue(self.channel.transport.disconnected)


    def test_connectionLost(self):
        """
        L{WebSocketHandler.connectionLost} is called with the reason of the
        connection closing when L{Request.connectionLost} is called.
        """
        self.request.connectionLost(Failure(CONNECTION_DONE))
        self.handler.lostReason.trap(ConnectionDone)

    def test_loseConnection_and_connectionLost(self):
        """
        L{Request.connectionLost} called after
        L{WebSocketTransport.loseConnection} does not cause problems.
        """
        self.handler.transport.loseConnection()
        # loseConnection() on the transport will disconnect the request, and it
        # will get its connectionLost method fired
        self.request.connectionLost(Failure(CONNECTION_DONE))
        self.assertTrue(self.channel.transport.disconnected)


class WebSocketHybiHandlerTestCase(TestCase):
    """
    Tests for L{WebSocketHandler} using the hybi-10 protocol.
    """

    def setUp(self):
        self.channel = DummyChannel()
        self.request = request = Request(self.channel, False)
        # Simulate request handling
        request.startedWriting = True
        transport = WebSocketHybiTransport(request)
        self.handler = TestHandler(transport)
        transport._attachHandler(self.handler)


    def test_write(self):
        """
        L{WebSocketHybiTransport.write} wraps the data in a text frame and
        writes it to the request.
        """
        self.handler.transport.write("Hello")
        self.handler.transport.write("World")
        self.assertEquals(
            self.channel.transport.written.getvalue(),
            "\x81\x05\x48\x65\x6c\x6c\x6f" + "\x81\x05\x57\x6f\x72\x6c\x64")
        self.assertFalse(self.channel.transport.disconnected)


    def test_sendFrame(self):
        """
        L{WebSocketHybiTransport.sendFrame} creates an unmasked hybi-10 frame
        and writes it to the request
        """
        self.handler.transport.sendFrame(OPCODE_PING, "ping")
        self.assertEquals(
            self.channel.transport.written.getvalue(),
            "\x89\x04\x70\x69\x6e\x67")
        self.assertFalse(self.channel.transport.disconnected)


    def test_sendLongFrame(self):
        """
        Sending a frame with a payload longer than 125 bytes results in a
        longer length field written to the request.
        """
        self.handler.transport.sendFrame(OPCODE_TEXT, "crap" * 20000)
        self.assertEquals(
            self.channel.transport.written.getvalue(),
            "\x81\x7f\x00\x00\x00\x00\x00\x01\x38\x80" + "crap" * 20000)
        self.assertFalse(self.channel.transport.disconnected)


    def test_sendFragmentedFrame(self):
        """
        Sending a frame with the fragmented flag makes the correct flag unset.
        """
        self.handler.transport.sendFrame(OPCODE_TEXT, "Hello", fragmented=True)
        self.assertEquals(
            self.channel.transport.written.getvalue(),
            "\x01\x05\x48\x65\x6c\x6c\x6f")
        self.assertFalse(self.channel.transport.disconnected)
