#!/usr/bin/env python
#
# Copyright (c) 2017 Michele Segata <segata@ccs-labs.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see http://www.gnu.org/licenses/.
#

import os
import sys
import ccparams as cc
import random
from utils import add_vehicle, set_par, change_lane, communicate, \
    get_distance, get_par, start_sumo, running

if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
import traci

# vehicle length
LENGTH = 4
# inter-vehicle distance
DISTANCE = 5
# inter-vehicle distance when leaving space for joining
JOIN_DISTANCE = DISTANCE * 2
# cruising speed
SPEED = 130/3.6

# maneuver states:
GOING_TO_POSITION = 0
OPENING_GAP = 1
COMPLETED = 2

# maneuver actors
LEADER = "v.0"
JOIN_POSITION = 3
FRONT_JOIN = "v.%d" % (JOIN_POSITION - 1)
BEHIND_JOIN = "v.%d" % JOIN_POSITION
N_VEHICLES = 8
JOINER = "v.%d" % N_VEHICLES

# sumo launch command
sumoBinary = "sumo-gui"
sumoCmd = [sumoBinary, "-D", "-c", "cfg/freeway.sumo.cfg"]


def add_vehicles(n, real_engine=False):
    """
    Adds a platoon of n vehicles to the simulation, plus an additional one
    farther away that wants to join the platoon
    :param n: number of vehicles of the platoon
    :param real_engine: set to true to use the realistic engine model,
    false to use a first order lag model
    :return: returns the topology of the platoon, i.e., a dictionary which
    indicates, for each vehicle, who is its leader and who is its front
    vehicle. The topology can the be used by the data exchange logic to
    automatically fetch data from leading and front vehicle to feed the CACC
    """
    # add a platoon of n vehicles
    topology = {}
    for i in range(n):
        vid = "v.%d" % i
        add_vehicle(vid, (n-i+1)*(DISTANCE+LENGTH) + 50, 0, SPEED, DISTANCE,
                    real_engine)
        change_lane(vid, 0)
        if i == 0:
            set_par(vid, cc.PAR_ACTIVE_CONTROLLER, cc.ACC)
        else:
            set_par(vid, cc.PAR_ACTIVE_CONTROLLER, cc.CACC)
        if i > 0:
            topology[vid] = {"front": "v.%d" % (i - 1), "leader": LEADER}
    # add a vehicle that wants to join the platoon
    vid = "v.%d" % n
    add_vehicle(vid, 10, 1, SPEED, DISTANCE, real_engine)
    change_lane(vid, 1)
    set_par(vid, cc.PAR_ACTIVE_CONTROLLER, cc.ACC)
    set_par(vid, cc.PAR_CACC_SPACING, JOIN_DISTANCE)
    return topology


def get_in_position(jid, fid, topology):
    """
    Makes the joining vehicle get close to the join position. This is done by
    changing the topology and setting the leader and the front vehicle for
    the joiner. In addition, we increase the cruising speed and we switch to
    the "fake" CACC, which uses a given GPS distance instead of the radar
    distance to compute the control action
    :param jid: id of the joiner
    :param fid: id of the vehicle that will become the predecessor of the joiner
    :param topology: the current platoon topology
    :return: the modified topology
    """
    topology[jid] = {"leader": LEADER, "front": fid}
    set_par(jid, cc.PAR_CC_DESIRED_SPEED, SPEED + 15)
    set_par(jid, cc.PAR_ACTIVE_CONTROLLER, cc.FAKED_CACC)
    return topology


def open_gap(vid, jid, topology, n):
    """
    Makes the vehicle that will be behind the joiner open a gap to let the
    joiner in. This is done by creating a temporary platoon, i.e., setting
    the leader of all vehicles behind to the one that opens the gap and then
    setting the front vehicle of the latter to be the joiner. To properly
    open the gap, the vehicle leaving space switches to the "fake" CACC,
    to consider the GPS distance to the joiner
    :param vid: vehicle that should open the gap
    :param jid: id of the joiner
    :param topology: the current platoon topology
    :param n: total number of vehicles currently in the platoon
    :return: the modified topology
    """
    index = int(vid.split(".")[1])
    for i in range(index + 1, n):
        # temporarily change the leader
        topology["v.%d" % i]["leader"] = vid
    # the front vehicle if the vehicle opening the gap is the joiner
    topology[vid]["front"] = jid
    set_par(vid, cc.PAR_ACTIVE_CONTROLLER, cc.FAKED_CACC)
    set_par(vid, cc.PAR_CACC_SPACING, JOIN_DISTANCE)
    return topology


def reset_leader(vid, topology, n):
    """
    After the maneuver is completed, the vehicles behind the one that opened
    the gap, reset the leader to the initial one
    :param vid: id of the vehicle that let the joiner in
    :param topology: the current platoon topology
    :param n: total number of vehicles in the platoon (before the joiner)
    :return: the modified topology
    """
    index = int(vid.split(".")[1])
    for i in range(index + 1, n):
        # temporarily change the leader
        topology["v.%d" % i]["leader"] = LEADER
    return topology


def main(demo_mode, real_engine, setter=None):
    # used to randomly color the vehicles
    random.seed(1)
    start_sumo("cfg/freeway.sumo.cfg", False)
    step = 0
    state = GOING_TO_POSITION
    while running(demo_mode, step, 6000):

        # when reaching 60 seconds, reset the simulation when in demo_mode
        if demo_mode and step == 6000:
            start_sumo("cfg/freeway.sumo.cfg", True)
            step = 0
            random.seed(1)

        traci.simulationStep()

        if step == 0:
            # create vehicles and track the joiner
            topology = add_vehicles(N_VEHICLES, real_engine)
            traci.gui.trackVehicle("View #0", JOINER)
            traci.gui.setZoom("View #0", 20000)
        if step % 10 == 1:
            # simulate vehicle communication every 100 ms
            communicate(topology)
        if step == 100:
            # at 1 second, let the joiner get closer to the platoon
            topology = get_in_position(JOINER, FRONT_JOIN, topology)
        if state == GOING_TO_POSITION:
            # when the distance of the joiner is small enough, let the others
            # open a gap to let the joiner enter the platoon
            if get_distance(JOINER, FRONT_JOIN) < JOIN_DISTANCE + 1:
                state = OPENING_GAP
                topology = open_gap(BEHIND_JOIN, JOINER, topology, N_VEHICLES)
        if state == OPENING_GAP:
            # when the gap is large enough, complete the maneuver
            if get_distance(BEHIND_JOIN, FRONT_JOIN) > 2 * JOIN_DISTANCE + 2:
                state = COMPLETED
                change_lane(JOINER, 0)
                set_par(JOINER, cc.PAR_ACTIVE_CONTROLLER, cc.CACC)
                set_par(JOINER, cc.PAR_CACC_SPACING, DISTANCE)
                set_par(BEHIND_JOIN, cc.PAR_ACTIVE_CONTROLLER, cc.CACC)
                set_par(BEHIND_JOIN, cc.PAR_CACC_SPACING, DISTANCE)
                topology = reset_leader(BEHIND_JOIN, topology, N_VEHICLES)
        if real_engine and setter is not None:
            # if we are running with the dashboard, update its values
            tracked_id = traci.gui.getTrackedVehicle("View #0")
            if tracked_id != "":
                (g, rpm) = cc.unpack(get_par(tracked_id, cc.PAR_ENGINE_DATA))
                data = get_par(tracked_id, cc.PAR_SPEED_AND_ACCELERATION)
                (v, a, u, x, y, t) = cc.unpack(data)
                setter(rpm, g, v, a)

        step += 1

    traci.close()


if __name__ == "__main__":
    main(True, True)