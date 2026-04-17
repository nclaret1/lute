"""Xtc1 zmq Sender. Intended to run in psana 1 environment.

Based on Mona's converter from https://github.com/monarin/xtc1to2
"""

import argparse
import csv
import json
import pickle
import zlib
from typing import Any, Callable, Dict, List, Tuple, TypedDict, Union, Type, cast

import numpy as np
import numpy.typing as npt
import psana  # type: ignore
import zmq
from PSCalib.GeometryAccess import GeometryAccess  # type: ignore

# Helper Classes
################

class DataSpec(TypedDict):
    xtc2_attr_name: str
    object_name: str
    object_type: str
    object_field_name: Union[str, Tuple[str, str]]


def get_geometry(geometry: GeometryAccess) -> Tuple[npt.NDArray[np.float32], npt.NDArray[np.uint16]]:
    """Return the pixel coordinates and index map.


    Args:
        geometry (GeometryAccess): A GeometryAccess object. This can be created
            for a detector (that has geometry) by calling geometry = det.geometry(evt).

    Returns:
        pixel_position (npt.NDArray[np.float32]): Pixel position array.

        pixel_index_map (npt.NDArray[np.uint16]): Pixel index array.
    """
    cframe: int = 0
    # Stores a tuple of x,y, and z coordinate arrays
    pixel_coords: tuple = geometry.get_pixel_coords(cframe=cframe)
    pixel_coord_indexes: tuple = geometry.get_pixel_coord_indexes(cframe=cframe)

    # Converts from microns to meters
    temp: List[np.ndarray[Any, np.dtype[np.float64]]] = [
        np.asarray(t) * 1e-6 for t in pixel_coords
    ]
    temp_index: List[np.ndarray[Any, np.dtype[np.float64]]] = [
        np.asarray(t) for t in pixel_coord_indexes
    ]

    # The shape of each axis is represented by five numbers (for this det)
    # e.g. (1,2,2,512,512). We calculate no. of panels by multiplying
    # all numbers except the last two (#pixel_x, #pixel_y).
    panel_num: np.integer = np.prod(temp[0].shape[:-2])

    shape: tuple = (panel_num, temp[0].shape[-2], temp[0].shape[-1])
    pixel_position: npt.NDArray[np.float32] = np.zeros(shape + (3,), dtype=np.float32)  # x,y,z
    pixel_index_map: npt.NDArray[np.uint16] = np.zeros(shape + (2,), dtype=np.uint16)  # x,y

    for n in range(3):
        pixel_position[..., n] = temp[n].reshape(shape).astype(np.float32)

    for n in range(2):
        pixel_index_map[..., n] = temp_index[n].reshape(shape).astype(np.uint16)

    return pixel_position, pixel_index_map


class ZmqSender:

    def __init__(self, socket: str) -> None:
        """
        A helper for sending messages using pyzmq.

        Args:
            socket (str): Socket string e.g. "tcp://127.0.0.1:5557"
        """
        context: zmq.Context = zmq.Context()
        self.zmq_socket: zmq.Socket = context.socket(zmq.PUSH)
        self.zmq_socket.bind(socket)

    def send_zipped_pickle(self, obj: dict, flags: int = 0, protocol: int = -1) -> None:
        """Pickle an object, and zip the pickle before sending it"""

        try:
            p: bytes = pickle.dumps(obj, protocol)
            z: bytes = zlib.compress(p)
            self.zmq_socket.send(z, flags=flags)
        except (pickle.PickleError, TypeError, zlib.error, zmq.ZMQError) as e:
            print(f"[XTC1 Sender]: Error during sending pickled object: {e}")

    def send_array(
        self, data: np.ndarray, flags: int = 0, copy: bool = True, track: bool = False
    ) -> None:
        """
        Send a NumPy array with metadata (dtype and shape) over a ZeroMQ socket.

        Args:
            data (np.ndarray): Array to send.

            flags (int): ZMQ flags (e.g., zmq.SNDMORE).

            copy (bool): Whether to copy the message.

            track (bool): Whether to track the message.

        Returns:
            bool: True if the message was successfully sent, False otherwise.
        """
        try:
            md: dict[str, Any] = {
                "dtype": str(data.dtype),
                "shape": data.shape,
            }

            # Send metadata then the actual array
            self.zmq_socket.send_json(md, flags | zmq.SNDMORE)
            self.zmq_socket.send(data, flags, copy=copy, track=track)

        except (AttributeError, zmq.ZMQError, TypeError, ValueError) as e:
            print(f"[XTC1 Sender]: Error, failed to send array: {e}")

    def close(self) -> None:
        """Closes the zmq socket"""
        self.zmq_socket.close()


def get_data(data_specs: List[DataSpec], evt: psana.Event) -> Any:
    data: Dict[str, Any] = {}
    for data_spec in data_specs:
        obj_type: Type = eval(data_spec["object_type"])
        obj: object = obj_type(data_spec["object_name"])
        # Because of round trip through JSON, this will be a list
        # hence the cast. True type is tuple
        field_name: Union[str, List[str]] = cast(
            Union[str, List[str]], data_spec["object_field_name"]
        )
        obj_field: Callable[[psana.Event], Any]
        attr_name: str = data_spec["xtc2_attr_name"]
        if isinstance(field_name, list):
            obj_field = getattr(obj, field_name[0])
            data[attr_name] = getattr(obj_field(evt), field_name[1])()
        else:
            obj_field = getattr(obj, field_name)
            data[attr_name] = obj_field(evt)
    return data


def get_calib_constants(data_specs: List[DataSpec], evt: psana.Event) -> Any:
    data: Dict[str, Any] = {}
    for data_spec in data_specs:
        # We assume currently that it is the same object in all specs
        # so only need to loop once
        obj_type: Type = eval(data_spec["object_type"])
        obj: object = obj_type(data_spec["object_name"])
        if hasattr(obj, "pedestals"):
            data["pedestals"] = obj.pedestals(evt)
        if hasattr(obj, "status"):
            data["pixel_status"] = obj.status(evt)
        if hasattr(obj, "status_as_mask"):
            data["mask"] = obj.status_as_mask(evt)
        if hasattr(obj, "gain"):
            data["gain"] = obj.gain(evt)
        if hasattr(obj, "geometry"):
            pixel_position, pixel_index_map = get_geometry(obj.geometry(evt))
            data["pixel_position"] = pixel_position
            data["pixel_index_map"] = pixel_index_map
        break
    return data if data else None


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="Xtc1 reader", description="Read in Xtc1 files using psana1"
    )
    parser.add_argument(
        "-a",
        "--access_pattern",
        type=str,
        help="JSON string for access pattern of data.",
    )
    parser.add_argument(
        "-e", "--exp", type=str, help="Experiment's name"
    )
    parser.add_argument(
        "-f",
        "--eventfile",
        type=str,
        help="File with the event numbers",
    )
    parser.add_argument(
        "-n",
        "--nevents",
        type=int,
        help=(
            "Specify the number of events. If both this and eventfile are provided, "
            "eventfile is used. If neither is provided, will convert all events."
        ),
        default=0,
    )
    parser.add_argument("-p", "--port", type=int, help="Port number.", default=5557)
    parser.add_argument("-r", "--run", type=str, help="Run number")

    args: argparse.Namespace = parser.parse_args()

    data_def: Dict[str, Any] = json.loads(args.access_pattern)

    socket: str = f"tcp://127.0.0.1:{args.port}"
    zmq_send: ZmqSender = ZmqSender(socket)

    mode: str = "idx"
    datasource_id: str = f"exp={args.exp}:run={args.run}:{mode}"
    datasource: psana.DataSource = psana.DataSource(datasource_id)
    run_current: psana.Run = next(datasource.runs())
    timestamps: tuple = run_current.times()
    # If the eventfile is presented select those, otherwise use all events
    event_num_list: List[int]

    if not args.eventfile:
        # All events
        if not args.nevents:
            event_num_list = list(range(len(timestamps)))
        else:
            event_num_list = list(range(args.nevents))
    else:
        event_num_list = []
        try:
            with open(args.eventfile, newline="") as csvfile:
                csvreader = csv.reader(csvfile, delimiter=",")
                for row in csvreader:
                    event_num_list += list(map(int, row))
        except FileNotFoundError:
            print(f"Error: File not found: {args.eventfile}, using test numbers.")
            event_num_list = [290, 291]

    data_type_info: Dict[str, Dict[str, Tuple[Type, int]]] = {}
    send_type_info: bool = True

    calib_dict: Dict[str, Any] = {}
    for i, event_num in enumerate(event_num_list):
        timestamp: psana.EventTime = timestamps[int(event_num)]
        event: psana.Event = run_current.event(timestamp)

        data: Dict[str, Any] = {}

        for detname in data_def:
            # detector data will be a dict of field_names to the data
            # (e.g. "calib": (1,2,3,...))
            detector_data: Dict[str, Any] = get_data(data_def[detname], event)
            data[detname] = detector_data
            if send_type_info:
                data_type_info[detname] = {}
                for attr_name, field_data in detector_data.items():
                    dtype: Type
                    rank: int
                    if isinstance(field_data, np.ndarray):
                        dtype = field_data.dtype.type
                        rank = field_data.ndim
                    elif isinstance(field_data, float):
                        dtype = np.float64
                        rank = 0
                    elif isinstance(field_data, int):
                        dtype = np.int64
                        rank = 0
                    else:
                        dtype = type(field_data)
                        rank = 0
                    data_type_info[detname][attr_name] = (dtype, rank)
                for detname in data_def:
                    calib_consts: Any = get_calib_constants(data_def[detname], event)
                    if calib_consts is not None:
                        calib_dict[detname] = calib_consts
                        calib_detname: str = f"{detname}_calib"
                        data_type_info[calib_detname] = {}
                        for attr_name, field_data in calib_consts.items():
                            if isinstance(field_data, np.ndarray):
                                dtype = field_data.dtype.type
                                rank = field_data.ndim
                            elif isinstance(field_data, float):
                                dtype = np.float64
                                rank = 0
                            elif isinstance(field_data, int):
                                dtype = np.int64
                                rank = 0
                            else:
                                dtype = type(field_data)
                                rank = 0
                            data_type_info[calib_detname][attr_name] = (dtype, rank)

        data["timestamp"] = timestamp.time()
        if send_type_info:
            zmq_send.send_zipped_pickle({"DATA_TYPE_INFO": data_type_info})
            send_type_info = False

        if i == 0:
            # Send beginning timestamp - this will create config, beginrun,
            # beginstep, and enable on the client.
            start_dict: Dict[str, Any] = {
                "start": True,
                "config_timestamp": timestamp.time() - 10,
            }
            if calib_dict:
                # If the requested detectors had calibration constants they will
                # be attached to the BeginRun transition as part of the scan det
                start_dict["calib_const"] = calib_dict
            print("[XTC1 Sender]: Starting sending..")
            zmq_send.send_zipped_pickle(start_dict)

        print(f"[XTC1 Sender]: event_num={event_num} ts={timestamp.time()}")

        # Send the dataset
        zmq_send.send_zipped_pickle(data)

    # Send end message
    done_dict: Dict[str, bool] = {"end": True}
    zmq_send.send_zipped_pickle(done_dict)
    print("[XTC1 Sender]: Sending complete!")

    zmq_send.close()

if __name__ == "__main__":
    main()
