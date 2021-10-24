import os
import shutil
import typing as t

from simple_di import Provide, inject

import bentoml._internal.constants as _const

from ._internal.configuration.containers import BentoMLContainer
from ._internal.models import SAVE_NAMESPACE
from ._internal.runner import Runner
from ._internal.utils import LazyLoader
from .exceptions import BentoMLException, MissingDependencyException

_exc = _const.IMPORT_ERROR_MSG.format(
    fwr="onnxruntime & onnx",
    module=__name__,
    inst="Refers to https://onnxruntime.ai/"
    " to correctly install backends options"
    " and platform suitable for your application usecase.",
)

if t.TYPE_CHECKING:  # pylint: disable=unused-import # pragma: no cover
    import onnx
    import onnxruntime
    from _internal.models.store import ModelInfo, ModelStore
else:
    onnx = LazyLoader("onnx", globals(), "onnx", exc_msg=_exc)
    onnxruntime = LazyLoader("onnxruntime", globals(), "onnxruntime", exc_msg=_exc)

try:
    from onnx.external_data_helper import load_external_data_for_model

except ImportError:  # pragma: no cover
    raise MissingDependencyException(
        """onnx is required in order to use the module `bentoml.onnx`, install
        onnx with `pip install sklearn`. For more information, refer to
        https://onnx.ai/get-started.html
        """
    )


# helper methods
def _yield_first_val(iterable):
    if isinstance(iterable, tuple):
        yield iterable[0]
    elif isinstance(iterable, str):
        yield iterable
    else:
        yield from iterable


def flatten_list(lst) -> t.List[str]:
    if not isinstance(lst, list):
        raise AttributeError
    return [k for i in lst for k in _yield_first_val(i)]


def _get_model_info(
    tag: str,
    model_store: "ModelStore",
) -> t.Tuple["ModelInfo", str, t.Dict[str, t.Any]]:
    model_info = model_store.get(tag)
    if model_info.module != __name__:
        raise BentoMLException(
            f"Model {tag} was saved with module {model_info.module}, failed loading "
            f"with {__name__}."
        )
    model_file = os.path.join(model_info.path, f"{SAVE_NAMESPACE}{ONNX_EXT}")
    return model_info, model_file


@inject
def load(  # pylint: disable=arguments-differ
    tag: str,
    backend: t.Optional[str] = "onnxruntime",
    providers: t.List[t.Union[str, t.Tuple[str, dict]]] = None,
    sess_opts: t.Options["onnxruntime.SessionOptions"] = None,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> "onnxruntime.InferenceSession":
    """
    Load a model from BentoML local modelstore with given name.

    Args:
        tag (`str`):
            Tag of a saved model in BentoML local modelstore.
        model_store (`~bentoml._internal.models.store.ModelStore`, default to `BentoMLContainer.model_store`):
            BentoML modelstore, provided by DI Container.

    Returns:
        an instance of Onnx model from BentoML modelstore.

    Examples::
    """  # noqa
    _, model_file = _get_model_info(tag, model_store)

    if backend not in SUPPORTED_ONNX_BACKEND:
        raise BentoMLException(
            f"'{backend}' runtime is currently not supported for ONNXModel"
        )
    if providers:
        if not all(
            i in onnxruntime.get_all_providers() for i in flatten_list(providers)
        ):
            raise BentoMLException(f"'{providers}' cannot be parsed by `onnxruntime`")
    else:
        providers = onnxruntime.get_available_providers()

    if isinstance(model_file, onnx.ModelProto):
        return onnxruntime.InferenceSession(
            model_file.SerializeToString(), sess_options=sess_opts, providers=providers
        )
    else:
        _get_path = os.path.join(model_file, f"{SAVE_NAMESPACE}{ONNX_EXT}")
        return onnxruntime.InferenceSession(
            _get_path, sess_options=sess_opts, providers=providers
        )


@inject
def save(
    name: str,
    model: t.Any,
    *,
    metadata: t.Union[None, t.Dict[str, t.Any]] = None,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> str:
    """
    Save a model instance to BentoML modelstore.

    Args:
        name (`str`):
            Name for given model instance. This should pass Python identifier check.
        model:
            Instance of model to be saved
        metadata (`t.Optional[t.Dict[str, t.Any]]`, default to `None`):
            Custom metadata for given model.
        model_store (`~bentoml._internal.models.store.ModelStore`, default to `BentoMLContainer.model_store`):
            BentoML modelstore, provided by DI Container.

    Returns:
        tag (`str` with a format `name:version`) where `name` is the defined name user
        set for their models, and version will be generated by BentoML.

    Examples::
    """  # noqa
    context = {"onnx": onnx.__version__}
    with model_store.register(
        name,
        module=__name__,
        metadata=metadata,
        framework_context=context,
    ) as ctx:
        if isinstance(model, onnx.ModelProto):
            onnx.save_model(
                model, os.path.join(ctx.path, f"{SAVE_NAMESPACE}{ONNX_EXT}")
            )
        else:
            shutil.copyfile(
                model, os.path.join(ctx.path, f"{SAVE_NAMESPACE}{ONNX_EXT}")
            )
        return ctx.tag


class _ONNXRunner(Runner):
    @inject
    def __init__(
        self,
        tag: str,
        backend: t.Optional[str] = "onnxruntime",
        providers: t.List[t.Union[str, t.Tuple[str, dict]]] = None,
        sess_opts: t.Options["onnxruntime.SessionOptions"] = None,
        resource_quota: t.Dict[str, t.Any] = None,
        batch_options: t.Dict[str, t.Any] = None,
        model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
    ):
        super().__init__(tag, resource_quota, batch_options)
        model_info, model_file = _get_model_info(tag, model_store)

        if backend not in SUPPORTED_ONNX_BACKEND:
            raise BentoMLException(
                f"'{backend}' runtime is currently not supported for ONNXModel"
            )
        if providers:
            if not all(
                i in onnxruntime.get_all_providers() for i in flatten_list(providers)
            ):
                raise BentoMLException(
                    f"'{providers}' cannot be parsed by `onnxruntime`"
                )
        else:
            providers = onnxruntime.get_available_providers()

        self._model_info = model_info
        self._model_file = model_file
        self._backend = backend
        self._providers = providers
        self._sess_opts = sess_opts

    @property
    def required_models(self) -> t.List[str]:
        return [self._model_info.tag]

    @property
    def num_concurrency_per_replica(self) -> int:
        if self.resource_quota.on_gpu:
            return 1
        return int(round(self.resource_quota.cpu))

    @property
    def num_replica(self) -> int:
        if self.resource_quota.on_gpu:
            return len(self.resource_quota.gpus)
        return 1

    # pylint: disable=arguments-differ,attribute-defined-outside-init
    def _setup(self) -> None:
        if isinstance(self._model_file, onnx.ModelProto):
            self._model = onnxruntime.InferenceSession(
                self._model_file.SerializeToString(),
                sess_options=self._sess_opts,
                providers=self._providers,
            )
        else:
            _path = os.path.join(self._model_file, f"{SAVE_NAMESPACE}{ONNX_EXT}")
            self._model = onnxruntime.InferenceSession(
                _path, sess_options=self._sess_opts, providers=self._providers
            )

    # pylint: disable=arguments-differ,attribute-defined-outside-init
    def _run_batch(self, input_data) -> t.Any:
        pass


@inject
def load_runner(
    tag: str,
    *,
    backend: t.Optional[str] = "onnxruntime",
    providers: t.List[t.Union[str, t.Tuple[str, dict]]] = None,
    sess_opts: t.Options["onnxruntime.SessionOptions"] = None,
    resource_quota: t.Union[None, t.Dict[str, t.Any]] = None,
    batch_options: t.Union[None, t.Dict[str, t.Any]] = None,
    model_store: "ModelStore" = Provide[BentoMLContainer.model_store],
) -> "_ONNXRunner":
    """
    Runner represents a unit of serving logic that can be scaled horizontally to
    maximize throughput. `bentoml.onnx.load_runner` implements a Runner class that
    wrap around an Onnx model, which optimize it for the BentoML runtime.

    Args:
        tag (`str`):
            Model tag to retrieve model from modelstore
        resource_quota (`t.Dict[str, t.Any]`, default to `None`):
            Dictionary to configure resources allocation for runner.
        batch_options (`t.Dict[str, t.Any]`, default to `None`):
            Dictionary to configure batch options for runner in a service context.
        model_store (`~bentoml._internal.models.store.ModelStore`, default to `BentoMLContainer.model_store`):
            BentoML modelstore, provided by DI Container.

    Returns:
        Runner instances for `bentoml.onnx` model

    Examples::
    """  # noqa
    return _ONNXRunner(
        tag=tag,
        backend=backend,
        providers=providers,
        sess_opts=sess_opts,
        resource_quota=resource_quota,
        batch_options=batch_options,
        model_store=model_store,
    )


# import os
# import shutil
# import typing as t

# import bentoml._internal.constants as _const

# from ._internal.models.base import MODEL_NAMESPACE, Model
# from ._internal.types import GenericDictType, PathType
# from ._internal.utils import LazyLoader
# from .exceptions import BentoMLException

# _exc = _const.IMPORT_ERROR_MSG.format(
#     fwr="onnxruntime & onnx",
#     module=__name__,
#     inst="Refers to https://onnxruntime.ai/"
#     " to correctly install backends options"
#     " and platform suitable for your application usecase.",
# )

# if t.TYPE_CHECKING:  # pylint: disable=unused-import # pragma: no cover
#     import onnx
#     import onnxruntime
# else:
#     onnx = LazyLoader("onnx", globals(), "onnx", exc_msg=_exc)
#     onnxruntime = LazyLoader("onnxruntime", globals(), "onnxruntime", exc_msg=_exc)


# def _yield_first_val(iterable):
#     if isinstance(iterable, tuple):
#         yield iterable[0]
#     elif isinstance(iterable, str):
#         yield iterable
#     else:
#         yield from iterable


# def flatten_list(lst) -> t.List[str]:
#     if not isinstance(lst, list):
#         raise AttributeError
#     return [k for i in lst for k in _yield_first_val(i)]


# class ONNXModel(Model):
#     """
#     Model class for saving/loading :obj:`onnx` models.

#     Args:
#         model (`str`):
#             Given filepath or protobuf of converted model.
#             Make sure to use corresponding library to convert
#             model from different frameworks to ONNX format.
#         backend (`str`, `optional`, default to `onnxruntime`):
#             Name of ONNX inference runtime. ["onnxruntime", "onnxruntime-gpu"]
#         metadata (`GenericDictType`,  `optional`, default to `None`):
#             Class metadata.

#     Raises:
#         MissingDependencyException:
#             :obj:`onnx` is required by ONNXModel
#         NotImplementedError:
#             :obj:`backend` as onnx runtime is not supported by ONNX
#         BentoMLException:
#             :obj:`backend` as onnx runtime is not supported by ONNXModel
#         InvalidArgument:
#             :obj:`path` passed in :meth:`~save` is not either
#              a :obj:`onnx.ModelProto` or filepath

#     Example usage under :code:`train.py`::

#         TODO:

#     One then can define :code:`bento.py`::

#         TODO:
#     """

#     SUPPORTED_ONNX_BACKEND: t.List[str] = ["onnxruntime", "onnxruntime-gpu"]
#     ONNX_EXTENSION: str = ".onnx"

#     def __init__(
#         self,
#         model: t.Union[PathType, "onnx.ModelProto"],
#         backend: t.Optional[str] = "onnxruntime",
#         metadata: t.Optional[GenericDictType] = None,
#     ):
#         super(ONNXModel, self).__init__(model, metadata=metadata)
#         if backend not in self.SUPPORTED_ONNX_BACKEND:
#             raise BentoMLException(
#                 f'"{backend}" runtime is currently not supported for ONNXModel'
#             )
#         self._backend = backend

#     @classmethod
#     def __get_model_fpath(cls, path: PathType) -> PathType:
#         return os.path.join(path, f"{MODEL_NAMESPACE}{cls.ONNX_EXTENSION}")

#     @classmethod
#     def load(  # pylint: disable=arguments-differ
#         cls,
#         path: t.Union[PathType, "onnx.ModelProto"],
#         backend: t.Optional[str] = "onnxruntime",
#         providers: t.List[t.Union[str, t.Tuple[str, dict]]] = None,
#         sess_opts: t.Optional["onnxruntime.SessionOptions"] = None,
#     ) -> "onnxruntime.InferenceSession":
#         if backend not in cls.SUPPORTED_ONNX_BACKEND:
#             raise BentoMLException(
#                 f'"{backend}" runtime is currently not supported for ONNXModel'
#             )
#         if providers is not None:
#             if not all(
#                 i in onnxruntime.get_all_providers() for i in flatten_list(providers)
#             ):
#                 raise BentoMLException(
#                     f"'{providers}' can't be parsed by `onnxruntime`"
#                 )
#         else:
#             providers = onnxruntime.get_available_providers()
#         if isinstance(path, onnx.ModelProto):
#             return onnxruntime.InferenceSession(
#                 path.SerializeToString(), sess_options=sess_opts, providers=providers
#             )
#         else:
#             _get_path = str(cls.__get_model_fpath(path))
#             return onnxruntime.InferenceSession(
#                 _get_path, sess_options=sess_opts, providers=providers
#             )

#     def save(self, path: t.Union[PathType, "onnx.ModelProto"]) -> None:
#         if isinstance(self._model, onnx.ModelProto):
#             onnx.save_model(self._model, self.__get_model_fpath(path))
#         else:
#             shutil.copyfile(self._model, str(self.__get_model_fpath(path)))


SUPPORTED_ONNX_BACKEND: t.List[str] = ["onnxruntime", "onnxruntime-gpu"]
ONNX_EXT: str = ".onnx"
