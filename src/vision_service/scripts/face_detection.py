## @package face_detection
#  This module processes realsense camera input and runs face detection, alignment and pose estimation.
#
#  Module Tasks:
#	- Main loop to process realsense camera input
#	- Run Face Detection based MTCNN for Joint Face Detection & Alignment for each frame
#	- Track Face over frames
#	- Calculate Face Position in 3D coordinates
# 	- Send ROS msg containing face area, key points and face pose and unique ID for each detected face
#
#	Current Workarounds:
#	- Tracking not implemented (no unique face id provided)
#	- 3D coodinates not implemented (face region used as distance measure)
#	- Function for Face Recognition also implemented in this modue for simplicity (to be put into anothe rmodule)
#	- No ROS communication

# basic imports
import sys
import numpy as np
import cv2
import os
from scipy import misc
import time
import re
import pickle
from thread import start_new_thread

# Import for Neural Networks
import tensorflow as tf
from models.mtcnn import detect_face

# Realsense libraries
import pyrealsense as pyrs

# Define of standard face size for alignment (EXPECT_SIZE x EXPECT_SIZE)
EXPECT_SIZE = 160

# Define bounding box size for proximity detetcion of faces (increase to make distance smaller)
FACE_AREA = 1500  # Face area for approx. 1.5m distance


## Function to do face detection and alignment on an image
#
#  Run face detection on the full input image using a MTCNN for Joint Detection and Alignment from here:
#  https://github.com/pangyupo/mxnet_mtcnn_face_detection
#  @param img The RGB image
#  @return Bounding boxes bb and landmark points for eyes, nose and mouth edges.
def detect_face_and_landmarks_mtcnn(img):
    img = img[:, :, 0:3]
    bbs, lms = detect_face.detect_face(img, minsize, pnet, rnet, onet,
                                       threshold, factor)
    boxes = []
    landmarks = []
    face_index = 0
    for r in bbs:
        r = r.astype(int)
        points = []
        for i in range(5):
            points.append((lms[i][face_index], lms[i + 5][face_index]))
        landmarks.append(points)
        boxes.append((r[0], r[1], r[2], r[3]))
        face_index += 1
    return boxes, landmarks


## Function to align detected faces.
#
#  The current implementation crops the picture given a face region. 
#  We do not use actual alignment because performance increase for face recognition is marginal and only 
#  slows down realtime performance as is also argued here:
#  https://github.com/davidsandberg/facenet/issues/93
#  @param img The RGB image
#  @param bb The bounding box of a face as tuple (x1, y1, x2, y2)
#  @return Returns the cropped face region.
def align_face_mtcnn(img, bb):
    assert isinstance(bb, tuple)
    cropped = img[bb[1]:bb[3], bb[0]:bb[2], :]
    scaled = misc.imresize(
        cropped, (EXPECT_SIZE, EXPECT_SIZE), interp='bilinear')
    return scaled


## Function to draw bounding boxes in a picture
#
#  Given an image, the bounding boxes for the corresponding face regions are drawn. Additionally a resize_factor
#  is used if the bounding boxes were calculated on a scaled version of the input image. Default value of the resize factor
#  is 1, meaning bounding boxes were calculated on the same image size.
#  @param img The RGB image
#  @param bbs An array of bounding boxes of a face as tuple (x1, y1, x2, y2)
#  @resize_factor factor to scale up bounding box size if calculated on different picture scale.
#  @return Image overlayed with the bounding boxes.
def draw_rects(img, bbs, resize_factor=1):
    result = img.copy()
    bbs = (np.array(bbs) / resize_factor).astype(int)
    for left, top, right, bottom in bbs:
        cv2.rectangle(result, (left, top), (right, bottom), (0, 255, 0), 2)
    return result


## Function to draw feature points in a picture
#
#  Given an image, the feature points for the corresponding faces are drawn. Additionally a resize_factor
#  is used if the feature points were calculated on a scaled version of the input image. Default value of the 
#  resize factor is 1, meaning the feature points were calculated on the same image size.
#  @param img The RGB image
#  @param points An array containing arrays of feature points of a face
#  @resize_factor factor to scale up bounding box size if calculated on different picture scale.
#  @return Image overlayed with the feature points
def draw_landmarks(img, points, resize_factor):
    result = img.copy()
    for face_points in points:
        for point in face_points:
            point = (int(point[0] / resize_factor), int(
                point[1] / resize_factor))
            cv2.circle(result, point, 3, (0, 255, 0), -1)
    return result


## Returns the closest face of all detected faces
#
#  Current implementation uses bounding box size to compare proximity
#  @param bbs An array of bounding boxes of a face as tuple (x1, y1, x2, y2).
#  @return The array index of the biggest bounding box.
def get_closest_face(bbs):
    i = 0
    max_id = 0
    face_area = 0
    for left, top, right, bottom in bbs:
        tmp_face_area = (right - left) * (bottom - top)
        if (tmp_face_area > face_area):
            max_id = i
            face_area = tmp_face_area
        i += 1
    return max_id


## Checks whether a face is visible within certain distance
#
#  Current implementation uses bounding box to check for proximity. 
#  Key value defined in FACE_AREA.
#  @param bbs An array of bounding boxes of a face as tuple (x1, y1, x2, y2).
#  @return True if a face is close enough, False otherwise
def face_detected(bbs):
    face_area = 0
    for left, top, right, bottom in bbs:
        tmp_face_area = (right - left) * (bottom - top)
        if (tmp_face_area > face_area):
            face_area = tmp_face_area
    if (face_area > FACE_AREA):
        return True
    else:
        return False


## Identifies a face using Facenet
#
#  TODO: To be moved into other module
#  The function calculates the 128D embeddings of a given face using facenet in this implementation:
#  https://github.com/davidsandberg/facenet
#  Then the embeddings are run through a SVM classifier to identify the face. 
#  @face_img The cropped image of the face region.
#  @param session The tensorflow session with FaceNet already loaded
#  @param classifier The SVM classifier already loaded
#  @return Return the name of the face.
def recognize_face(face_img, session, classifier):

    # calculate 128D embeddings
    feed_dict = {
        image_batch: np.expand_dims(face_img, 0),
        phase_train_placeholder: False
    }
    rep = session.run(embeddings, feed_dict=feed_dict)[0]

    # get class probabilities using SVM classifier
    probabilities = classifier.predict_proba(rep.reshape(1, -1))

    # Calculate most likely class
    out = np.argmax(probabilities[0])

    # Retrieve class name
    names = np.load('models/own_embeddings/own_names.npy')
    face_name = names[out]

    print('classification: ' + face_name + ' probability: ' +
          probabilities[0][out])
    return face_name


## Function to load a tensorflow model
#
#  TODO: To be moved into other module
#  @param model_dir model directory
#  @param model_meta meta file
#  @param model_content checpoint file
#  @return Returns a tensorflow session
def load_model(model_dir, model_meta, model_content):
    session = tf.InteractiveSession()
    model_dir_exp = os.path.expanduser(model_dir)
    saver = tf.train.import_meta_graph(os.path.join(model_dir_exp, meta_file))
    saver.restore(tf.get_default_session(),
                  os.path.join(model_dir_exp, ckpt_file))
    tf.get_default_graph().as_graph_def()
    return session


## Helper Function to load a tensorlow model
#
#  TODO: To be moved into other module
#  The function finds the meta_file and checkpoint within a given path
#  @param model_dir Path where the model is stored
#  @return Returns meta_file and checkpoint
def get_model_filenames(model_dir):
    files = os.listdir(model_dir)
    meta_files = [s for s in files if s.endswith('.meta')]
    if len(meta_files) == 0:
        raise ValueError(
            'No meta file found in the model directory (%s)' % model_dir)
    elif len(meta_files) > 1:
        raise ValueError(
            'There should not be more than one meta file in the model directory (%s)'
            % model_dir)
    meta_file = meta_files[0]
    meta_files = [s for s in files if '.ckpt' in s]
    max_step = -1
    for f in files:
        step_str = re.match(r'(^model-[\w\- ]+.ckpt-(\d+))', f)
        if step_str is not None and len(step_str.groups()) >= 2:
            step = int(step_str.groups()[1])
            if step > max_step:
                max_step = step
                ckpt_file = step_str.groups()[0]
    return meta_file, ckpt_file


## Entry Point to run face detection.
#
#  Loads all data and processes realsense camera input in a loop.
if __name__ == '__main__':

    # start pyrealsense service
    pyrs.start()

    #Image Size (define size of image)
    x_pixel = 640
    y_pixel = 480

    # resize for faster processing
    resize_factor = 0.5

    # store whether a face was detected nearby
    face_nearby = False

    # store how many following frames no Face was detected nearby
    # used to be more resistant for single frames with missing face detection.
    no_face_detect_counter = 0

    # init realsense device
    dev = pyrs.Device(
        device_id=0,
        streams=[
            pyrs.ColourStream(width=x_pixel, height=y_pixel, fps=30),
            pyrs.DepthStream()
        ])

    # Init MTCNN for Face Detection
    sess = tf.Session(config=tf.ConfigProto(log_device_placement=False))
    pnet, rnet, onet = detect_face.create_mtcnn(sess, None)
    minsize = 20  # minimum size of face
    threshold = [0.6, 0.7, 0.7]  # three steps's threshold
    factor = 0.709  # scale factor

    # Init Facenet for face recognition
    print('Initializing Facenet...')
    tree_model = "models/Tree/own.mod"
    svm_model = "models/SVM/svm_lfw.mod"
    clf = pickle.load(open(tree_model, "rb"))
    model_dir = 'models/facenet'
    meta_file, ckpt_file = get_model_filenames(os.path.expanduser(model_dir))
    session = load_model(model_dir, meta_file, ckpt_file)
    graph = tf.get_default_graph()
    image_batch = graph.get_tensor_by_name("input:0")
    phase_train_placeholder = graph.get_tensor_by_name("phase_train:0")
    embeddings = graph.get_tensor_by_name("embeddings:0")
    print('done.')

    print('Starting detection...')
    while True:
        # Get frame from realsense
        dev.wait_for_frame()
        # color image  
        c = cv2.cvtColor(dev.colour, cv2.COLOR_RGB2BGR)
        #depth images
        d = dev.depth * dev.depth_scale * 1000

        #resize images for faster processing with resize_factor
        img = cv2.resize(c, (int(resize_factor * x_pixel), int(
            resize_factor * y_pixel)))

        d_img = cv2.resize(d, (int(resize_factor * x_pixel), int(
            resize_factor * y_pixel)))
        d_img = cv2.applyColorMap(d.astype(np.uint8), cv2.COLORMAP_RAINBOW)

        # Detect and align faces using MTCNN
        total_boxes, points = detect_face_and_landmarks_mtcnn(img)

        # If no faces were found (= no bounding boxes) just show frame and continie loop
        if len(total_boxes) is 0:
            no_face_detect_counter += 1
            if no_face_detect_counter > 3:
                face_nearby = False
            # show image and continue
            cv2.imshow("detection result", c)
            cv2.waitKey(10)
            continue

        # Check if faces nearby
        if face_detected(total_boxes):
            face_nearby = True
        else:
            no_face_detect_counter += 1
            if no_face_detect_counter > 3:
                face_nearby = False

        # TODO: Trigger Face Recognition only on service request
        start_recognize_face = false
        if start_recognize_face:
            start_new_thread(recognize_face, (align_face_mtcnn(
                img, total_boxes[get_closest_face(total_boxes)]), session,
                                              clf, ))

        # TODO:
        # - create ros service returning face_nearby
        # - create ros service calling recognize_face(face_img, session, classifier) and returning classification result

        #Show detection result
        draw = draw_rects(c.copy(), total_boxes, resize_factor)
        draw = draw_landmarks(draw, points, resize_factor)
        cv2.imshow("detection result", draw)

        #WAIT
        cv2.waitKey(10)
