#!/usr/bin/env python
"""
Main script.

This script put all the pieces togetehr and performs the main demo of the
project. The script will take the responsibility to check if a user entered
the scene, if Baxter owns money to the customer or the customer owns money
to Baxter and trigger the appropriate algorithms to serve the customer.

    Copyright (C)  2016/2017 The University of Leeds and Rafael Papallas

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

# System specific imports
import copy
import threading
import time

import baxter_interface
from baxter_interface import CHECK_VERSION

# ROS specific imports
import rospy
import cv2
import cv_bridge
import rospkg

from sensor_msgs.msg import (Image,)

# Project specific imports
from baxter_cashier_manipulation.srv import GetUserPose
from baxter_cashier_manipulation.srv import RecogniseBanknote
from baxter_pose import BaxterPose
from moveit_controller import MoveItPlanner


class Banknote:
    """This class represent a single banknote on the table."""

    def __init__(self, pose):
        """Default constructor."""
        self.pose = pose
        self.is_available = True


class BanknotesOnTable:
    """This class represents the banknotes on the one side of the table."""

    def __init__(self, initial_pose, table_side, num_of_remaining_banknotes):
        """Default constructor."""
        self._table_side = table_side
        self._initial_pose = initial_pose
        self._number_of_remaining_banknotes = num_of_remaining_banknotes
        self.banknotes = [Banknote(initial_pose)]

        self._calculate_pose_of_remaining_poses()

    def is_left(self):
        """
        Will check if the side of the table is the left one.

        Will return True if is the left side of the table, false otherwise.
        """
        return True if self._table_side == "left" else False

    def is_right(self):
        """
        Will check if the side of the table is the right one.

        Will return True if is the left side of the table, false otherwise.
        """
        return True if self._table_side == "right" else False

    def reset_availability_for_all_banknotes(self):
        """
        Will reset the availability of all banknotes.

        Each banknote has a flag variable called "is_available", which flip to
        false if Baxter pick it, so Baxter is aware of which ones are not
        on the table.

        This function will reset all of them to true, assuming that the
        operator has also return the banknotes to the posistions.
        """
        for banknote in self.banknotes:
            banknote.is_available = True

    def get_next_available_banknote(self):
        """Will return the next available banknote on the table."""
        for banknote in self.banknotes:
            if banknote.is_available:
                banknote.is_available = False
                return banknote

        return None

    def _calculate_pose_of_remaining_poses(self):
        """
        Will calculate the remaining banknotes on the table.

        This will calculate the poses of the remaining banknotes on the table.
        """
        static_x_to_be_added = 0.10  # 10cm

        for _ in range(0, self._number_of_remaining_banknotes):
            new_pose = copy.deepcopy(self.banknotes[-1])
            new_pose.pose.transformation_x += static_x_to_be_added
            self.banknotes.append(new_pose)


class Cashier:
    """
    Main script that put everything together.

    This script is the main logic of the project, it uses all the pieces to
    achieve the end result, from picking money, returning change, doing the
    money recognition etc.
    """

    def __init__(self):
        """Default constructor that setup the environemnt."""
        # Initialisation
        rs = baxter_interface.RobotEnable(CHECK_VERSION)
        init_state = rs.state().enabled
        rs.enable()

        # This is the camera topic to be used for money recognition (Baxter's
        # head camera or RGB-D camera)
        self._money_recognition_camera_topic = "/cameras/head_camera/image"

        # Baxter's libms configured
        self.planner = MoveItPlanner()

        # TODO: Make this zero, is just for testing purposes set to 3
        self.amount_due = 3
        self.customer_last_pose = None

        self.banknotes_table_left = self.set_banknotes_on_table(side="left")
        self.banknotes_table_right = self.set_banknotes_on_table(side="right")

        self.banknote_recognition_task_completed = False

    def set_banknotes_on_table(self, side):
        """
        Will record and calculate the poses of the banknotes on the table.

        This function will ask two questions from the user:
        (1) To move Baxter's arm to the position of the first banknote.
        (2) The number of the remaining banknotes on the table.

        It will then record the pose and also calculate the poses of the
        remaining banknotes.

        This function will also move Baxter's arms to the remaining banknotes
        just to ensure that the remaining banknotes are placed correctly.

        Finally the returned list of poses will include a list tuple
        (pose, bool) for every banknote on the table of either left or right
        side. The `pose` is the pose of the corresponding banknote, where the
        bool value (initially set to True) represents if Baxter have used this
        banknote.
        """
        if side == "left":
            arm = self.planner.left_arm
        else:
            arm = self.planner.right_arm

        print("==============================================================")
        print(" Calibrating poses of banknotes of the table's side: " + side)
        print("==============================================================")

        print("1. Move Baxter's {} hand above the first banknote".format(side))
        raw_input("Press ENTER to set the pose...")
        initial_pose = self.planner.get_end_effector_current_pose(side)

        # Calculate the remaining poses
        num = int(raw_input("2. Number of REMAINING banknotes on this side of \
                            the table? : "))

        # Create the table with the banknotes. This will also auto-calculate
        # the poses of the remaining banknotes on the table.
        banknotes_on_table = BanknotesOnTable(initial_pose=initial_pose,
                                              table_side=side,
                                              num_of_remaining_banknotes=num)

        # To ensure that the poses were calculated correctly, this will go
        # through the remaining banknotes and will move baxter there to show
        # to user exactly what the pose of the remaining banknotes is.
        for banknote in banknotes_on_table.banknotes[1:]:
            self.planner.move_to_position(banknote.pose, arm)
            rospy.sleep(1)

        # Once calibration is done, will move Baxter's arm back to normal pose
        self.planner.active_hand = arm
        self.planner.set_neutral_position_of_limb()

        return banknotes_on_table

    def interact_with_customer(self):
        """
        Main interaction logic.

        Handles the main logic of detecting the entrance of new customer, and
        determining if the next action is to get or give money.

        This will run until the amount due variable (self.amount_due) becomes
        zero. Therefore, before calling this function, ensure that you have
        changed the self.amount_due variable to either a positive or negative
        value.
        """
        def pose_is_outdated(pose):
            """Will check whether the pose is recent or not."""
            return (time.time() - pose.created) > 3

        # Make Baxter's screen eyes to shown normal
        self.show_eyes_normal()

        # Since we have new iteration here, ensure that the position of the
        # banknotes on the table is reset to normal.
        self.banknotes_table_left.reset_availability_for_all_banknotes()
        self.banknotes_table_right.reset_availability_for_all_banknotes()

        # Do this while customer own money or baxter owns money
        while self.amount_due != 0:
            print self.amount_due

            # If the amount due is negative, Baxter owns money
            if self.amount_due < 0:
                self.give_money_to_customer()
                continue

            # Get the hand pose of customer's two hands.
            left_pose, right_pose = self.get_pose_from_space()

            # If the pose detected is not too recent, ignore.
            if pose_is_outdated(left_pose) and pose_is_outdated(right_pose):
                continue

            # NOTE that we use right hand for left pose and left hand for right
            # pose. Baxter's left arm is closer to user's right hand and vice
            # versa.
            if self.pose_is_reachable(left_pose, "right"):
                self.take_money_from_customer(left_pose,
                                              self.planner.right_arm)

            elif self.pose_is_reachable(right_pose, "left"):
                self.take_money_from_customer(right_pose,
                                              self.planner.left_arm)

            else:
                print("Wasn't able to move hand to goal position")

    def pose_is_reachable(self, pose, side):
        """Will check whether the given pose is reachable."""
        arm = self.planner.right_arm

        if side == "left":
            arm = self.planner.left_arm

        if not pose.is_empty():
            # Verify that Baxter can move there
            is_reachable = self.planner.is_pose_reachable_by_arm(pose, arm)
            return is_reachable

        return False

    def take_money_from_customer(self, pose, arm):
        """Will take money from the customer."""
        # Move there to get the money from customer's hand.
        self.planner.move_to_position(pose, arm)

        # Open/Close the Gripper to catch the money from customer's hand
        self.planner.open_gripper()
        rospy.sleep(1)
        self.planner.close_gripper()

        # Moves Baxter hand to head for money recognition
        self.planner.move_hand_to_head_camera()

        # Here show Baxter's eyes moving to show that the robot is not stuck
        # but is instead "thinking" (because eyes are moving)
        self.run_nonblocking(self.make_eyes_animated_reading_banknote)

        # Start reading the banknote value using money recognition
        banknote_value = self.get_banknote_value()

        if banknote_value != -1:
            # Show image of the recognised banknote.
            image = "one_bill_recognised.png"
            if banknote_value == 5:
                image = "five_bill_recognised.png"

            self.show_image_to_baxters_head_screen(image)

            # Since we detected amount, subtract the value from the own amount
            self.amount_due -= int(banknote_value)
            self.customer_last_pose = (pose, arm)
            self.planner.leave_banknote_to_the_table()
            rospy.sleep(1)
        else:
            self.show_image_to_baxters_head_screen("unable_to_recognise.png")

        self.show_eyes_normal()
        self.planner.set_neutral_position_of_limb()

    def get_banknote_value(self):
        """"Will do the money recognition and will return the detected amount.

        This will either return a correct amount like 1 or 5 but also -1 if
        nothing detected.
        """
        self.banknote_recognition_task_completed = False

        # This method blocks until the service 'get_user_pose' is available
        rospy.wait_for_service('recognise_banknote')

        try:
            # Handle for calling the service
            recognise_banknote = rospy.ServiceProxy('recognise_banknote',
                                                    RecogniseBanknote)

            # Use the handle as any other normal function
            value = recognise_banknote(self._money_recognition_camera_topic)
            self.banknote_recognition_task_completed = True
            return value.banknote_amount
        except rospy.ServiceException as e:
            print("Service call failed: %s" % e)

        self.banknote_recognition_task_completed = True
        return None

    def run_nonblocking(self, function):
        """
        Will run a function in a separate thread.

        Usually needed for the "eye animation" which does not need to block
        the entire program, but needs to happen at the same time with another
        task.
        """
        thread = threading.Thread(target=function)
        thread.start()

    def make_eyes_animated_reading_banknote(self):
        """Will create the illusion that the eyes are moving."""
        funcs = [self.show_eyes_focusing,
                      self.show_eyes_focusing_right,
                      self.show_eyes_focusing_left,
                      self.show_eyes_focusing_right,
                      self.show_eyes_focusing_left]

        for func in funcs:
            if self.banknote_recognition_task_completed == False:
                func()

    def show_eyes_normal(self):
        """Will show normal eyes to Baxter's screen."""
        self.show_image_to_baxters_head_screen("normal_eyes.png")

    def show_eyes_focusing(self):
        """Will show focusing eyes to Baxter's screen."""
        self.show_image_to_baxters_head_screen("looking_eyes.png")

    def show_eyes_focusing_left(self):
        """Will show focusing eyes looking to left to Baxter's screen."""
        self.show_image_to_baxters_head_screen("looking_left_eyes.png")

    def show_eyes_focusing_right(self):
        """Will show focusing eyes looking to right to Baxter's screen."""
        self.show_image_to_baxters_head_screen("looking_right_eyes.png")

    def show_image_to_baxters_head_screen(self, image_path):
        """Will show an image to Baxter's screen."""
        rospack = rospkg.RosPack()
        path = rospack.get_path('baxter_cashier_manipulation')
        img = cv2.imread(path + "/img/" + image_path)
        msg = cv_bridge.CvBridge().cv2_to_imgmsg(img, encoding="bgr8")

        pub = rospy.Publisher('/robot/xdisplay',
                              Image,
                              latch=True,
                              queue_size=2)
        pub.publish(msg)
        # Sleep to allow for image to be published
        rospy.sleep(1)

    def pick_banknote_from_table(self, arm):
        """
        Will pick a banknote from the table.

        Will find the next available banknote from the table and will pick it
        up.
        """
        self.planner.active_hand = arm
        self.planner.open_gripper()

        # Find the next available banknote from the table.
        if arm.is_left():
            banknote = self.banknotes_table_left.get_next_available_banknote()
        elif arm.is_right():
            banknote = self.banknotes_table_right.get_next_available_banknote()

        # If one is available.
        if banknote is not None:
            # Go there and pick it up.

            # Create a new pose from the banknote pose, just to make sure
            # Baxter first move a bit above the banknote and then actually
            # pick it.
            banknote_above = copy.deepcopy(banknote)
            banknote_above.pose.transformation_z += 0.10
            self.planner.move_to_position(banknote_above.pose, arm)

            # Now actually move exactly where the pose is to pick the banknote
            self.planner.move_to_position(banknote.pose, arm)
            self.planner.close_gripper()
            self.planner.set_neutral_position_of_limb()
        else:
            print("No available banknotes on the table...")

    def give_money_to_customer(self):
        """Will return change to the customer."""
        customer_hand_pose, baxter_arm = self.customer_last_pose
        money_to_give_back = 1

        # Pick banknote from the table.
        self.pick_banknote_from_table(baxter_arm)

        # Move torwards to customer's hand.
        self.planner.move_to_position(customer_hand_pose,
                                      baxter_arm)

        # Waiting user to reach the robot to get the money
        rospy.sleep(1)
        self.planner.open_gripper()

        # Now that the user got his banknote update the amount due variable.
        self.amount_due += money_to_give_back

        # If amount is not negative, then move the hand to neutral position.
        if self.amount_due >= 0:
            self.planner.set_neutral_position_of_limb()

    def get_pose_from_space(self):
        """Will return the user's hand-pose from space."""
        # This method blocks until the service 'get_user_pose' is available
        rospy.wait_for_service('get_user_pose')

        try:
            # Handle for calling the service
            get_user_pose = rospy.ServiceProxy('get_user_pose', GetUserPose)

            # Use the handle as any other normal function
            # IMPORTANT: Note that for some reason the Skeelton Tracker library
            # identifies the left hand as the right and the right as left,
            # hence an easy and quick fix was to request the opposite hand here
            left_hand = get_user_pose(user_number=1, body_part='right_hand')
            right_hand = get_user_pose(user_number=1, body_part='left_hand')
        except rospy.ServiceException as e:
            print("Service call failed: %s" % e)

        # Left hand pose
        x1, y1, z1 = left_hand.transformation
        x2, y2, z2, w = left_hand.rotation
        left_hand_pose = BaxterPose(x1, y1, z1, x2, y2, z2, w)

        # Right hand pose
        x1, y1, z1 = right_hand.transformation
        x2, y2, z2, w = right_hand.rotation
        right_hand_pose = BaxterPose(x1, y1, z1, x2, y2, z2, w)

        return left_hand_pose, right_hand_pose


if __name__ == '__main__':
    rospy.init_node("baxter_cashier")
    cashier = Cashier()

    while True:
        cashier.interact_with_customer()
