"""In-memory protocol fixture, NOT evidence of a real S3 provider's guarantees."""
import io
import threading

from botocore.exceptions import ClientError


class Events:
    def register(self, *args, **kwargs):
        pass


class MemoryS3:
    def __init__(self):
        self.objects, self.uploads, self.aborted, self.calls = {}, {}, [], []
        self.lock = threading.Lock()
        self.meta = type("Meta", (), {"events": Events()})()

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        with self.lock:
            if Key in self.objects:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
            self.objects[Key] = Body.read()
        self.calls.append("put")
        return {}

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
        value = self.objects[Key]
        return {"ContentLength": len(value), "Body": io.BytesIO(value)}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)
        return {}

    def create_multipart_upload(self, *, Bucket, Key, **kwargs):
        upload_id = str(len(self.uploads) + 1)
        self.uploads[upload_id] = {}
        return {"UploadId": upload_id}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        self.uploads[UploadId][PartNumber] = Body
        return {"ETag": str(PartNumber)}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch):
        assert IfNoneMatch == "*"
        with self.lock:
            if Key in self.objects:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "CompleteMultipartUpload")
            self.objects[Key] = b"".join(self.uploads[UploadId][part["PartNumber"]] for part in MultipartUpload["Parts"])
        self.uploads.pop(UploadId)
        self.calls.append("complete")
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self.aborted.append(UploadId)
        self.uploads.pop(UploadId, None)
        return {}
