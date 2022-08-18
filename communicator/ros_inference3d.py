import sys
import rospy
from sensor_msgs.msg import Image, PointCloud2
from cv_bridge import CvBridge
try:
    import ros_numpy
except Exception as E:
    print('[WARNING] {}'.format(E))

import cv2
import numpy as np

from tritonclient.grpc import service_pb2, service_pb2_grpc
import tritonclient.grpc.model_config_pb2 as mc
from .channel import grpc_channel
from .base_inference import BaseInference
# from utils import image_util


class RosInference3D(BaseInference):

    """
    A RosInference to support ROS input and provide input to channel for inference.
    """

    def __init__(self, channel, client):
        '''
            channel: channel of type communicator.channel
            client: client of type clients

        '''

        super().__init__(channel, client)

        self.image = None
        self.br = CvBridge()

        self._register_inference() # register inference based on type of client
        self.client_postprocess = client.get_postprocess() # get postprocess of client
        self.client_preprocess = client.get_preprocess()
        self.class_names = self.client_postprocess.load_class_names()

    def _register_inference(self):
        """
        register inference
        """
        # for GRPC channel
        if type(self.channel) == grpc_channel.GRPCChannel:
            self._set_grpc_channel_members()
        else:
            pass

        self.detection = rospy.Publisher(self.channel.params['pub_topic'], Image, queue_size=10)

    def _set_grpc_channel_members(self):
        """
            Set properties for grpc channel, queried from the server.
        """
        # collect meta data of model and configuration
        meta_data = self.channel.get_metadata()
        self.channel.input, self.channel.output, self.input_types = self.client.parse_model(
            meta_data["metadata_response"], meta_data["config_response"].config)
        
        self.output0 = service_pb2.ModelInferRequest().InferRequestedOutputTensor() # boxes
        self.output0.name = self.channel.output[0]
        self.output1 = service_pb2.ModelInferRequest().InferRequestedOutputTensor() # class_IDs
        self.output1.name = self.channel.output[1]
        self.output2 = service_pb2.ModelInferRequest().InferRequestedOutputTensor() # scores
        self.output2.name = self.channel.output[2]

        self.channel.request.outputs.extend([self.output0,
                                             self.output1,
                                             self.output2])

        self.input0 = service_pb2.ModelInferRequest().InferInputTensor() # voxels
        self.input0.name = self.channel.input[0]
        self.input0.datatype = self.input_types[0]
        tmp = 10000
        self.input0.shape.extend([tmp, 32, 4])
        self.input1 = service_pb2.ModelInferRequest().InferInputTensor() # coors
        self.input1.name = self.channel.input[1]
        self.input1.datatype = self.input_types[1]
        self.input1.shape.extend([tmp, 4])
        self.input2 = service_pb2.ModelInferRequest().InferInputTensor() # num_points
        self.input2.name = self.channel.input[2]
        self.input2.datatype = self.input_types[2]
        self.input2.shape.extend([tmp])
        self.channel.request.inputs.extend([self.input0, self.input1, self.input2])

    def start_inference(self):
        rospy.Subscriber(self.channel.params['sub_topic'], PointCloud2, self._pc_callback)
        rospy.spin()

    def _scale_boxes(self, box, normalized=False):
        '''
        box: Bounding box generated for the image size (e.g. 512 x 512) expected by the model at triton server
        return: Scaled bounding box according to the input image from the ros topic.
        '''
        if normalized:
            # TODO make it dynamic with mc.Modelshape according to CHW or HWC
            xtl, xbr = box[0] * self.orig_size[1], box[2] * self.orig_size[1]
            ytl, ybr = box[1] * self.orig_size[0], box[3] * self.orig_size[0]
        else:
            xtl, xbr = box[0] * (self.orig_size[1] / self.input_size[0]), \
                       box[2] * (self.orig_size[1] / self.input_size[0])
            ytl, ybr = box[1] * self.orig_size[0] / self.input_size[1], \
                       box[3] * self.orig_size[0] / self.input_size[1]

        return [xtl, ytl, xbr, ybr]

    def _pc_callback(self, msg):
        self.pc = ros_numpy.point_cloud2.pointcloud2_to_xyz_array(msg)
        self.pc = self.client_preprocess.filter_pc(self.pc)
        # the number of voxels changes every sample
        num_voxels = self.pc['voxels'].shape[0]
        # num_voxels = 10000
        self.input0.ClearField("shape")
        self.input1.ClearField("shape")
        self.input2.ClearField("shape")
        self.input0.shape.extend([num_voxels, 32, 4])
        self.input1.shape.extend([num_voxels, 4])
        self.input2.shape.extend([num_voxels])
        # self.channel.request.ClearField("inputs")
        self.channel.request.ClearField("raw_input_contents")  # Flush the previous image contents
        # voxels = self.pc['voxels']
        # coors = self.pc['voxel_coords']
        # num_points = np.array(self.pc['voxel_num_points'], dtype=np.int32)
        
        # DUMMY Input
        from numpy import random
        voxels = random.rand(num_voxels, 32, 4)
        coors = np.random.randint(1,100, size=(num_voxels,4), dtype=np.int32)
        num_points = np.random.randint(1,1000, size=(num_voxels,), dtype=np.int32)
        self.channel.request.raw_input_contents.extend([voxels.tobytes(), coors.tobytes(), num_points.tobytes()])
        self.channel.response = self.channel.do_inference() # perform the channel Inference
        self.output = self.client_postprocess.extract_boxes(self.channel.response)
        print('hold')
