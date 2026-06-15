class BaseStreamHandler:
    """Base for multiplexed WS stream handlers. Subclass and implement on_connect/disconnect/receive."""

    stream_name: str = NotImplemented

    def __init__(self, consumer):
        self.consumer = consumer
        self.channel_layer = consumer.channel_layer
        self.channel_name = consumer.channel_name

    async def on_connect(self, user):
        pass

    async def on_disconnect(self, user):
        pass

    async def on_receive(self, user, data: dict):
        pass

    async def send(self, payload: dict):
        await self.consumer.send_to_client(self.stream_name, payload)
