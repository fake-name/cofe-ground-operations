# controller.py
# layer of abstraction for moving motors

import circle
import ephem
import math
import string
import time
import threading

class Controller:
    
    def __init__ (self, logger, galil, converter, config):
        self.logger = logger
        self.galil = galil
        self.converter = converter
        self.config = config
        
        self.scan_queue = 0 # number of scans left to do
    
    
    # current_pos: retrieve current motor positions
    # -> azimuth, altitude
    def current_pos (self):
        pos_enc = list(self.galil.pos)
        
        azimuth = ephem.degrees(self.converter.encoder_to_az(
            pos_enc[string.uppercase.index(self.galil.axis_az)]))
        altitude = ephem.degrees(self.converter.encoder_to_el(
            pos_enc[string.uppercase.index(self.galil.axis_el)]))
        
        return math.degrees(azimuth), math.degrees(altitude)
    
    
    # scan: generic scan function
    #   (note: should be executed in a maximum of one thread at any time)
    #
    #   crd_list -> list([crd_a, crd_b])
    #     crd_a = coordinate that goes from 0 to 360 degrees
    #     crd_b = coordinate that goes from -90 to 90 degrees
    #   process_func: function to process list of points
    #     (optionally, use "process_hor" and "process_equ" below)
    #   repeat: number of times to repeat (use "True" for indefinite repetition)
    def scan (self, crd_list, process_func, repeat = 1):
        
        # unset any previously set stop events
        self.stop = threading.Event()
        
        # repeat indefinitely
        if str(repeat) == str(True):
            self.scan_queue = 1
        else: # repeat for <repeat> times
            self.scan_queue = repeat
        
        # queue and process scan
        while self.scan_queue > 0 and not self.stop.is_set():
            
            # process forward scan and wait until scan is complete
            dist = process_func(crd_list)
            
            # quit if we only need to process one direction
            if self.scan_queue <= 0.5 or self.stop.is_set():
                break
            
            # reverse direction and repeat
            crd_list.reverse()
            
            # reset direction again and prepare for next time
            crd_list.reverse()
            if str(repeat) != str(True):
                self.scan_queue = self.scan_queue - 1
        
        self.logger.info("scan complete")
        return 0
        
    # process_hor: process a list of horizontal coordinates to slew to
    #   crd_list -> list([azi, alt]): list of coordinates to slew to (degrees)
    def process_hor (self, crd_list):
        i = 0
        
        prev_pt = self.current_pos()
        
        # loop through all segments
        while len(crd_list) > i and not self.stop.is_set():
            self.goto(crd_list[i], begin=prev_pt)
            prev_pt = crd_list[i]
            i = i + 1
    
    
    # process_equ: process a list of equatorial coordinates to slew to
    #   crd_list -> list([ra, de]): list of coordinates to slew to (degrees)
    def process_equ (self, crd_list):
        i = 0
        
        # start with current motor position
        prev_pt = self.current_pos()
        
        # loop through all segments
        while len(crd_list) > i and not self.stop.is_set():
            
            ##
            # iteratively compute the altitude and azimuth of a set of
            # equatorial coordinates at the time which we will reach that
            # point with the telescope -- ie. the current altitude and
            # azimuth of a particular set of a particular set of equatorial
            # coordinates will no longer be current by the time we get
            # there -- we need to find the new coordinates before moving
            ##
            dt0 = 0
            
            # continue looping until we've converged close enough to the
            #  actual amount of time it takes to reach our target point
            while True:
                
                # compute estimate of distance to the next point
                azi, alt = self.converter.radec_to_azel(
                    math.radians(crd_list[i][0]), math.radians(crd_list[i][1]), dt0)
                delta = circle.distance(
                    [math.degrees(azi), math.degrees(alt)], prev_pt)
                
                # estimate of time needed to get to next point
                dt = delta / float(self.config.get("slew", "speed"))
                if math.fabs(dt - dt0) < 0.01:
                    break # accurate enough, stop loop
                
                # not accurate enough, continue looping
                dt0 = dt
            
            # compute horizontal coordinates of equatorial coordinates after
            #  time dt has passed (the point where we should slew to)
            azi, alt = self.converter.radec_to_azel(
                math.radians(crd_list[i][0]), math.radians(crd_list[i][1]), dt)
            new_crd_h = [math.degrees(azi), math.degrees(alt)]
            
            # move to new position and reset for the next iteration
            self.goto(new_crd_h, begin=prev_pt)
            prev_pt = new_crd_h
            i = i + 1
    
    
    # goto: slew to a particular coordinate from current position
    #
    #   hor_pos -> [azimuth, altitude]: new position to slew to
    #   begin -> [azimuth, altitude]: where to start slewing from
    #
    # -> (returns when tracking ends)
    def goto (self, hor_pos, begin=None):
        self.logger.info("slew to " + str(hor_pos[0]) + ", " + str(hor_pos[1]))
        
        # angular distance and bearing to new point
        begin = begin or self.current_pos() # start at current pos if none given
        ang_dist = circle.distance(begin, hor_pos)
        bearing = circle.bearing(begin, hor_pos)
        
        # generate list of intermediate points to slew to
        num_int = int(ang_dist) # one intermediate point per degree
        prev_pt = begin # assume we're at the starting point
        
        if num_int > 0:
            point_list = []
            
            for i in range(1, num_int):
                a, b = circle.waypoint(begin, bearing, i * ang_dist / num_int)
                point_list.append([a, b])
            
            # interval between intermediate points
            speed = float(self.config.get("slew", "speed"))
            delta = ang_dist / num_int # angular separation between points
            tm_step = delta / speed # time step in seconds
            
            # sample time (convert seconds->samples)
            samp_per_sec = 1024000.0 / 1000.0 # TODO: compute from actual sample time
            tm_st_samp = tm_step * samp_per_sec # time step in samples
            tm_st_pow2 = int(math.log(tm_st_samp, 2)) # as a power of 2
            tm_st = max(0, min(8, tm_st_pow2)) # constrain to range [0, 8]
            # TODO: use steps that are actually powers of 2 and resize
            
            # enter contour mode
            self.galil.sendOnly("CM " + self.galil.axis_az + self.galil.axis_el)
            self.galil.sendOnly("DT " + str(tm_st))
            
            # slew to all intermediate points
            for pt in point_list:
                
                # find shortest azimuth direction
                if abs((pt[0] - 360.0) - prev_pt[0]) < abs(pt[0] - prev_pt[0]):
                    pt[0] -= 360.0
                elif abs((pt[0] + 360) - prev_pt[0]) < abs(pt[0] - prev_pt[0]):
                    pt[0] += 360.0
                
                # get speed of axes
                d_az = pt[0] - prev_pt[0]
                d_el = pt[1] - prev_pt[1]
                sp_az = d_az * math.cos(math.radians(pt[1])) / delta * speed
                sp_el = d_el / delta * speed
                
                # smoothly transition to new state
                self.galil.sendOnly("CD " + 
                    str(self.converter.az_to_encoder(d_az)) + "," + # azimuth
                    str(self.converter.el_to_encoder(d_el)))        # altitude
                
                prev_pt = pt
            
            # run all items and exit contour mode
            self.galil.sendOnly("CD 0,0,0,0=0")
            self.galil.sendOnly("#Wait;JP#Wait,_CM<>511")
            
            self.galil.sendOnly("AM") # stall until motion is complete
        
        # determine relative azimuth distance to final position
        d_az = (hor_pos[0] - prev_pt[0]) % 360.0 # [0, 360.0)
        
        if d_az > 180.0:
            d_az -= 360.0 # (-180, 180] 
        
        # move to final position
        self.galil.sendOnly("PR" + self.galil.axis_az + "=" +
            str(self.converter.az_to_encoder(d_az)))
        self.galil.sendOnly("PA" + self.galil.axis_el + "=" +
            str(self.converter.el_to_encoder(hor_pos[1])))
        
        self.galil.sendOnly("BG")
        self.galil.sendOnly("AM") # stall until motion is complete
        
        # wait for slew to finish
        time.sleep(ang_dist / float(self.config.get("slew", "speed")))
        # TODO: use more accurate time indicator
        
        # update the current position in case of wrap-around
        self.sync(hor_pos)
        
        return 0
    
    
    # track: follow an equatorial position indefinitely
    #
    #   equ_pos -> [ra, de]: position to track
    #
    # -> (returns once tracking ends)
    def track (self, equ_pos):
        
        # unset any previously set stop events
        self.stop = threading.Event()
        
        # move to initial position quickly
        self.galil.sendOnly("SP" + self.galil.axis_az + "=" +
            str(self.converter.az_to_encoder(float(self.config.get("slew", "speed")))))
        self.galil.sendOnly("SP" + self.galil.axis_el + "=" +
            str(self.converter.el_to_encoder(float(self.config.get("slew", "speed")))))
        # TODO: set speed as combined speed of both axes
        
        accel_az = str(self.converter.az_to_encoder(float(self.config.get("slew", "accel"))))
        accel_el = str(self.converter.el_to_encoder(float(self.config.get("slew", "accel"))))
        self.galil.sendOnly("AC" + self.galil.axis_az + "=" + accel_az)
        self.galil.sendOnly("AC" + self.galil.axis_el + "=" + accel_el)
        self.galil.sendOnly("DC" + self.galil.axis_az + "=" + accel_az)
        self.galil.sendOnly("DC" + self.galil.axis_el + "=" + accel_el)
        
        azi, alt = self.converter.radec_to_azel(
            math.radians(equ_pos[0]), math.radians(equ_pos[1]))
        hor_pos = [math.degrees(azi), math.degrees(alt)]
        
        self.galil.sendOnly("PA" + self.galil.axis_az + "=" +
            str(self.converter.az_to_encoder(hor_pos[0])))
        self.galil.sendOnly("PA" + self.galil.axis_el + "=" +
            str(self.converter.el_to_encoder(hor_pos[1])))
        self.galil.sendOnly("BG")
        self.galil.sendOnly("AM") # stall until motion is complete
        
        # enable tracking mode
        self.galil.sendOnly("PT 1,1,1,1")
        
        # slew to equatorial coordinate and loop until self.stop is set
        while not self.stop.is_set():
            
            # compute new position
            old_pos = hor_pos
            azi, alt = self.converter.radec_to_azel(
                math.radians(equ_pos[0]), math.radians(equ_pos[1]))
            hor_pos = [math.degrees(azi), math.degrees(alt)]
            
            # compute motor velocities
            speed = circle.distance(old_pos, hor_pos) # per 1 second
            bearing = circle.bearing(old_pos, hor_pos)
            speed_az = math.fabs(speed * math.cos(math.radians(bearing))) \
                / (math.cos(math.radians(hor_pos[1])) + 0.01)
            speed_el = math.fabs(speed * math.sin(math.radians(bearing)))
            
            # adjust motor speed
            self.galil.sendOnly("SP" + self.galil.axis_az + "=" +
                str(10 * max(1, self.converter.az_to_encoder(speed_az))))
            self.galil.sendOnly("SP" + self.galil.axis_el + "=" +
                str(10 * max(1, self.converter.el_to_encoder(speed_el))))
            
            # move to new position
            self.galil.sendOnly("PA" + self.galil.axis_az + "=" +
                str(self.converter.az_to_encoder(hor_pos[0])))
            self.galil.sendOnly("PA" + self.galil.axis_el + "=" +
                str(self.converter.el_to_encoder(hor_pos[1])))
            
            time.sleep(1) # wait 1 second to update again
        
        # exit tracking mode
        self.galil.sendOnly("ST")
    
    
    # sync: set current position of motors
    #   hor_pos -> [az, el]: position to set motor position to
    def sync (self, hor_pos):
        
        # define current position as the given position
        self.galil.sendOnly("DP" + self.galil.axis_az + "=" +
            str(self.converter.az_to_encoder(hor_pos[0])))
        self.galil.sendOnly("DP" + self.galil.axis_el + "=" +
            str(self.converter.el_to_encoder(hor_pos[1])))
