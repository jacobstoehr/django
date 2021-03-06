import asyncio
import sys
from unittest import skipIf

from asgiref.testing import ApplicationCommunicator

from django.core.asgi import get_asgi_application
from django.core.signals import request_started
from django.db import close_old_connections
from django.test import AsyncRequestFactory, SimpleTestCase, override_settings

from .urls import test_filename


@skipIf(sys.platform == 'win32' and (3, 8, 0) < sys.version_info < (3, 8, 1), 'https://bugs.python.org/issue38563')
@override_settings(ROOT_URLCONF='asgi.urls')
class ASGITest(SimpleTestCase):
    async_request_factory = AsyncRequestFactory()

    def setUp(self):
        request_started.disconnect(close_old_connections)

    def tearDown(self):
        request_started.connect(close_old_connections)

    async def test_get_asgi_application(self):
        """
        get_asgi_application() returns a functioning ASGI callable.
        """
        application = get_asgi_application()
        # Construct HTTP request.
        scope = self.async_request_factory._base_scope(path='/')
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({'type': 'http.request'})
        # Read the response.
        response_start = await communicator.receive_output()
        self.assertEqual(response_start['type'], 'http.response.start')
        self.assertEqual(response_start['status'], 200)
        self.assertEqual(
            set(response_start['headers']),
            {
                (b'Content-Length', b'12'),
                (b'Content-Type', b'text/html; charset=utf-8'),
            },
        )
        response_body = await communicator.receive_output()
        self.assertEqual(response_body['type'], 'http.response.body')
        self.assertEqual(response_body['body'], b'Hello World!')

    async def test_file_response(self):
        """
        Makes sure that FileResponse works over ASGI.
        """
        application = get_asgi_application()
        # Construct HTTP request.
        scope = self.async_request_factory._base_scope(path='/file/')
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({'type': 'http.request'})
        # Get the file content.
        with open(test_filename, 'rb') as test_file:
            test_file_contents = test_file.read()
        # Read the response.
        response_start = await communicator.receive_output()
        self.assertEqual(response_start['type'], 'http.response.start')
        self.assertEqual(response_start['status'], 200)
        self.assertEqual(
            set(response_start['headers']),
            {
                (b'Content-Length', str(len(test_file_contents)).encode('ascii')),
                (b'Content-Type', b'text/plain' if sys.platform == 'win32' else b'text/x-python'),
                (b'Content-Disposition', b'inline; filename="urls.py"'),
            },
        )
        response_body = await communicator.receive_output()
        self.assertEqual(response_body['type'], 'http.response.body')
        self.assertEqual(response_body['body'], test_file_contents)
        # Allow response.close() to finish.
        await communicator.wait()

    async def test_headers(self):
        application = get_asgi_application()
        communicator = ApplicationCommunicator(
            application,
            self.async_request_factory._base_scope(
                path='/meta/',
                headers=[
                    [b'content-type', b'text/plain; charset=utf-8'],
                    [b'content-length', b'77'],
                    [b'referer', b'Scotland'],
                    [b'referer', b'Wales'],
                ],
            ),
        )
        await communicator.send_input({'type': 'http.request'})
        response_start = await communicator.receive_output()
        self.assertEqual(response_start['type'], 'http.response.start')
        self.assertEqual(response_start['status'], 200)
        self.assertEqual(
            set(response_start['headers']),
            {
                (b'Content-Length', b'19'),
                (b'Content-Type', b'text/plain; charset=utf-8'),
            },
        )
        response_body = await communicator.receive_output()
        self.assertEqual(response_body['type'], 'http.response.body')
        self.assertEqual(response_body['body'], b'From Scotland,Wales')

    async def test_get_query_string(self):
        application = get_asgi_application()
        for query_string in (b'name=Andrew', 'name=Andrew'):
            with self.subTest(query_string=query_string):
                scope = self.async_request_factory._base_scope(
                    path='/',
                    query_string=query_string,
                )
                communicator = ApplicationCommunicator(application, scope)
                await communicator.send_input({'type': 'http.request'})
                response_start = await communicator.receive_output()
                self.assertEqual(response_start['type'], 'http.response.start')
                self.assertEqual(response_start['status'], 200)
                response_body = await communicator.receive_output()
                self.assertEqual(response_body['type'], 'http.response.body')
                self.assertEqual(response_body['body'], b'Hello Andrew!')

    async def test_disconnect(self):
        application = get_asgi_application()
        scope = self.async_request_factory._base_scope(path='/')
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({'type': 'http.disconnect'})
        with self.assertRaises(asyncio.TimeoutError):
            await communicator.receive_output()

    async def test_wrong_connection_type(self):
        application = get_asgi_application()
        scope = self.async_request_factory._base_scope(path='/', type='other')
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({'type': 'http.request'})
        msg = 'Django can only handle ASGI/HTTP connections, not other.'
        with self.assertRaisesMessage(ValueError, msg):
            await communicator.receive_output()

    async def test_non_unicode_query_string(self):
        application = get_asgi_application()
        scope = self.async_request_factory._base_scope(path='/', query_string=b'\xff')
        communicator = ApplicationCommunicator(application, scope)
        await communicator.send_input({'type': 'http.request'})
        response_start = await communicator.receive_output()
        self.assertEqual(response_start['type'], 'http.response.start')
        self.assertEqual(response_start['status'], 400)
        response_body = await communicator.receive_output()
        self.assertEqual(response_body['type'], 'http.response.body')
        self.assertEqual(response_body['body'], b'')
