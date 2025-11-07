class NoAvailableWorkerError(Exception):
    def __init__(self, message="No available worker to handle the request", request_id=None):
        self.message = message
        self.request_id = request_id
        full_message = message
        if request_id:
            full_message += f" (Request ID: {request_id})"
        super().__init__(full_message)
