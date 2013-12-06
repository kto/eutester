#!/usr/bin/env python
#
#
# Description:  This script encompasses test cases/modules concerning instance specific behavior and
#               features for Eucalyptus.  The test cases/modules that are executed can be 
#               found in the script under the "tests" list.

import time
from concurrent.futures import ThreadPoolExecutor
import threading
from eutester.euca.euca_ops import Eucaops
from eutester.aws.ec2.instance import Instance
from eutester.utils.testcase import EutesterTestCase
from eutester.aws.ec2.ec2_ops import EC2ops
import os
import re
import random


class InstanceBasics(EutesterTestCase):
    def __init__( self, name="InstanceBasics", credpath=None, region=None, config_file=None, password=None, emi=None, zone=None,
                  user_data=None, instance_user=None, **kwargs):
        """
        EC2 API tests focused on instance store instances

        :param credpath: Path to directory containing eucarc file
        :param region: EC2 Region to run testcase in
        :param config_file: Configuration file path
        :param password: SSH password for bare metal machines if config is passed and keys arent synced
        :param emi: Image id to use for test
        :param zone: Availability Zone to run test in
        :param user_data: User Data to pass to instance
        :param instance_user: User to login to instance as
        :param kwargs: Additional arguments
        """
        super(InstanceBasics, self).__init__(name=name)
        if region:
            self.tester = EC2ops(credpath=credpath, region=region)
        else:
            self.tester = Eucaops(config_file=config_file, password=password, credpath=credpath)
        self.instance_timeout = 480

        ### Add and authorize a group for the instance
        self.group = self.tester.ec2.add_group(group_name="group-" + str(time.time()))
        self.tester.ec2.authorize_group_by_name(group_name=self.group.name)
        self.tester.ec2.authorize_group_by_name(group_name=self.group.name, port=-1, protocol="icmp" )
        ### Generate a keypair for the instance
        self.keypair = self.tester.ec2.add_keypair( "keypair-" + str(time.time()))
        self.keypath = '%s/%s.pem' % (os.curdir, self.keypair.name)
        if emi:
            self.image = emi
        else:
            self.image = self.tester.ec2.get_emi(root_device_type="instance-store",not_location="loadbalancer")
        self.address = None
        self.volume = None
        self.private_addressing = False
        if not zone:
            zones = self.tester.ec2.get_zones()
            self.zone = random.choice(zones)
        else:
            self.zone = zone
        self.reservation = None
        self.reservation_lock = threading.Lock()
        self.run_instance_params = {'image': self.image, 'user_data': user_data, 'username': instance_user,
                                'keypair': self.keypair.name, 'group': self.group.name,'zone': self.zone,
                                'timeout': self.instance_timeout}
        self.managed_network = True

        ### If I have access to the underlying infrastructure I can look
        ### at the network mode and only run certain tests where it makes sense
        if hasattr(self.tester,"service_manager"):
            cc = self.tester.get_component_machines("cc")[0]
            network_mode = cc.sys("cat " + self.tester.eucapath + "/etc/eucalyptus/eucalyptus.conf | grep MODE")[0]
            if re.search("(SYSTEM|STATIC)", network_mode):
                self.managed_network = False

    def set_reservation(self, reservation):
        self.reservation_lock.acquire()
        self.reservation = reservation
        self.reservation_lock.release()

    def clean_method(self):
        self.tester.cleanup_artifacts()

    def BasicInstanceChecks(self):
        """
        This case was developed to run through a series of basic instance tests.
             The tests are as follows:
                   - execute run_instances command
                   - make sure that public DNS name and private IP aren't the same
                       (This is for Managed/Managed-NOVLAN networking modes)
                   - test to see if instance is ping-able
                   - test to make sure that instance is accessible via ssh
                       (ssh into instance and run basic ls command)
             If any of these tests fail, the test case will error out, logging the results.
        """
        reservation = self.tester.ec2.run_instance(**self.run_instance_params)
        for instance in reservation.instances:
            self.assertTrue( self.tester.ec2.wait_for_reservation(reservation) ,'Instance did not go to running')
            self.assertTrue( self.tester.ping(instance.ip_address), 'Could not ping instance')
            self.assertFalse( instance.found("ls -1 /dev/" + instance.rootfs_device + "2",  "No such file or directory"),  'Did not find ephemeral storage at ' + instance.rootfs_device + "2")
        self.set_reservation(reservation)
        return reservation

    def ElasticIps(self):
        """
       This case was developed to test elastic IPs in Eucalyptus. This test case does
       not test instances that are launched using private-addressing option.
       The test case executes the following tests:
           - allocates an IP, associates the IP to the instance, then pings the instance.
           - disassociates the allocated IP, then pings the instance.
           - releases the allocated IP address
       If any of the tests fail, the test case will error out, logging the results.
        """
        if not self.reservation:
            reservation = self.tester.ec2.run_instance(**self.run_instance_params)
        else:
            reservation = self.reservation

        for instance in reservation.instances:
            if instance.ip_address == instance.private_ip_address:
                self.tester.info("WARNING: System or Static mode detected, skipping ElasticIps")
                return reservation
            self.address = self.tester.ec2.allocate_address()
            self.assertTrue(self.address,'Unable to allocate address')
            self.tester.ec2.associate_address(instance, self.address)
            instance.update()
            self.assertTrue( self.tester.ping(instance.ip_address), "Could not ping instance with new IP")
            self.tester.ec2.disassociate_address_from_instance(instance)
            self.tester.ec2.release_address(self.address)
            self.address = None
            assert isinstance(instance, Instance)
            self.tester.sleep(5)
            instance.update()
            self.assertTrue( self.tester.ping(instance.ip_address), "Could not ping after dissassociate")
        self.set_reservation(reservation)
        return reservation

    def MultipleInstances(self):
        """
        This case was developed to test the maximum number of m1.small vm types a configured
        cloud can run.  The test runs the maximum number of m1.small vm types allowed, then
        tests to see if all the instances reached a running state.  If there is a failure,
        the test case errors out; logging the results.
        """
        if self.reservation:
            self.tester.ec2.terminate_instances(self.reservation)
            self.set_reservation(None)

        reservation = self.tester.ec2.run_instance(min=2, max=2, **self.run_instance_params)
        self.assertTrue(self.tester.ec2.wait_for_reservation(reservation) ,'Not all instances  went to running')
        self.set_reservation(reservation)
        return reservation

    def LargestInstance(self):
        """
        This case was developed to test the maximum number of c1.xlarge vm types a configured
        cloud can run.  The test runs the maximum number of c1.xlarge vm types allowed, then
        tests to see if all the instances reached a running state.  If there is a failure,
        the test case errors out; logging the results.
        """
        if self.reservation:
            self.tester.ec2.terminate_instances(self.reservation)
            self.set_reservation(None)
        reservation = self.tester.ec2.run_instance(type="c1.xlarge", **self.run_instance_params)
        self.assertTrue( self.tester.ec2.wait_for_reservation(reservation) ,'Not all instances  went to running')
        self.set_reservation(reservation)
        return reservation

    def MetaData(self):
        """
        This case was developed to test the metadata service of an instance for consistency.
        The following meta-data attributes are tested:
           - public-keys/0/openssh-key
           - security-groups
           - instance-id
           - local-ipv4
           - public-ipv4
           - ami-id
           - ami-launch-index
           - reservation-id
           - placement/availability-zone
           - kernel-id
           - public-hostname
           - local-hostname
           - hostname
           - ramdisk-id
           - instance-type
           - any bad metadata that shouldn't be present.
        Missing nodes
         ['block-device-mapping/',  'ami-manifest-path']
        If any of these tests fail, the test case will error out; logging the results.
        """
        if not self.reservation:
            reservation = self.tester.ec2.run_instance(**self.run_instance_params)
        else:
            reservation = self.reservation
        for instance in reservation.instances:
            ## Need to verify  the public key (could just be checking for a string of a certain length)
            self.assertTrue(re.match(instance.get_metadata("public-keys/0/openssh-key")[0].split('eucalyptus.')[-1], self.keypair.name), 'Incorrect public key in metadata')
            self.assertTrue(re.match(instance.get_metadata("security-groups")[0], self.group.name), 'Incorrect security group in metadata')
            # Need to validate block device mapping
            #self.assertTrue(re.search(instance_ssh.get_metadata("block-device-mapping/")[0], "")) 
            self.assertTrue(re.match(instance.get_metadata("instance-id")[0], instance.id), 'Incorrect instance id in metadata')
            self.assertTrue(re.match(instance.get_metadata("local-ipv4")[0] , instance.private_ip_address), 'Incorrect private ip in metadata')
            self.assertTrue(re.match(instance.get_metadata("public-ipv4")[0] , instance.ip_address), 'Incorrect public ip in metadata')
            self.assertTrue(re.match(instance.get_metadata("ami-id")[0], instance.image_id), 'Incorrect ami id in metadata')
            self.assertTrue(re.match(instance.get_metadata("ami-launch-index")[0], instance.ami_launch_index), 'Incorrect launch index in metadata')
            self.assertTrue(re.match(instance.get_metadata("reservation-id")[0], reservation.id), 'Incorrect reservation in metadata')
            self.assertTrue(re.match(instance.get_metadata("placement/availability-zone")[0], instance.placement), 'Incorrect availability-zone in metadata')
            self.assertTrue(re.match(instance.get_metadata("kernel-id")[0], instance.kernel),  'Incorrect kernel id in metadata')
            self.assertTrue(re.match(instance.get_metadata("public-hostname")[0], instance.public_dns_name), 'Incorrect public host name in metadata')
            self.assertTrue(re.match(instance.get_metadata("local-hostname")[0], instance.private_dns_name), 'Incorrect private host name in metadata')
            self.assertTrue(re.match(instance.get_metadata("hostname")[0], instance.private_dns_name), 'Incorrect host name in metadata')
            self.assertTrue(re.match(instance.get_metadata("ramdisk-id")[0], instance.ramdisk ), 'Incorrect ramdisk in metadata') #instance-type
            self.assertTrue(re.match(instance.get_metadata("instance-type")[0], instance.instance_type ), 'Incorrect instance type in metadata')
            BAD_META_DATA_KEYS = ['foobar']
            for key in BAD_META_DATA_KEYS:
                self.assertTrue(re.search("Not Found", "".join(instance.get_metadata(key))), 'No fail message on invalid meta-data node')
        self.set_reservation(reservation)
        return reservation

    def DNSResolveCheck(self, zone=None):
        """
        This case was developed to test DNS resolution information for public/private DNS
        names and IP addresses.  The tested DNS resolution behavior is expected to follow
        AWS EC2.  The following tests are ran using the associated meta-data attributes:
           - check to see if Eucalyptus Dynamic DNS is configured
           - nslookup on hostname; checks to see if it matches local-ipv4
           - nslookup on local-hostname; check to see if it matches local-ipv4
           - nslookup on local-ipv4; check to see if it matches local-hostname
           - nslookup on public-hostname; check to see if it matches local-ipv4
           - nslookup on public-ipv4; check to see if it matches public-host
        If any of these tests fail, the test case will error out; logging the results.
        """
        if zone is None:
            zone = self.zone
        if not self.reservation:
            reservation = self.tester.ec2.run_instance(**self.run_instance_params)
        else:
            reservation = self.reservation

        for instance in reservation.instances:
            if not re.search("internal", instance.private_dns_name):
                self.tester.info("Did not find instance DNS enabled, skipping test")
                self.set_reservation(reservation)
                return reservation
            # Test to see if Dynamic DNS has been configured #
            # Per AWS standard, resolution should have private hostname or private IP as a valid response
            # Perform DNS resolution against public IP and public DNS name
            # Perform DNS resolution against private IP and private DNS name
            # Check to see if nslookup was able to resolve
            assert isinstance(instance, Instance)
            self.info("Check nslookup to resolve public DNS Name to local-ipv4 address")
            self.assertTrue(instance.found("nslookup " + instance.public_dns_name + " " + self.tester.ec2.connection.host, instance.private_ip_address), "Incorrect DNS resolution for hostname.")
            self.info("Check nslookup to resolve local-ipv4 address to private DNS name")
            self.assertTrue(instance.found("nslookup " +  instance.private_ip_address + " " + self.tester.ec2.connection.host, instance.private_dns_name), "Incorrect DNS resolution for private IP address")
            if self.managed_network:
                self.info("Check nslookup to resolve public-ipv4 address to public DNS name")
                self.assertTrue( instance.found("nslookup " +  instance.ip_address + " " + self.tester.ec2.connection.host, instance.public_dns_name), "Incorrect DNS resolution for public IP address")
                self.info("Check nslookup to resolve local-ipv4 address to private DNS name")
                self.assertTrue(instance.found("nslookup " + instance.private_dns_name + " " + self.tester.ec2.connection.host, instance.private_ip_address), "Incorrect DNS resolution for private hostname.")
        self.assertTrue(self.tester.ping(instance.public_dns_name))
        self.set_reservation(reservation)
        return reservation

    def Reboot(self, zone=None):
        """
        This case was developed to test IP connectivity and volume attachment after
        instance reboot.  The following tests are done for this test case:
                   - creates a 1 gig EBS volume, then attach volume
                   - reboot instance
                   - attempts to connect to instance via ssh
                   - checks to see if EBS volume is attached
                   - detaches volume
                   - deletes volume
        If any of these tests fail, the test case will error out; logging the results.
        """
        if zone is None:
            zone = self.zone
        if not self.reservation:
            reservation = self.tester.ec2.run_instance(**self.run_instance_params)
        else:
            reservation = self.reservation
        for instance in reservation.instances:
            ### Create 1GB volume in first AZ
            volume = self.tester.ec2.create_volume(instance.placement,size=1, timepergig=180)
            volume_device = instance.attach_volume(volume)
            ### Reboot instance
            instance.reboot_instance_and_verify(waitconnect=20)
            instance.detach_euvolume(volume)
            self.tester.ec2.delete_volume(volume)
        self.set_reservation(reservation)
        return reservation

    def Churn(self):
        """
        This case was developed to test robustness of Eucalyptus by starting instances,
        stopping them before they are running, and increase the time to terminate on each
        iteration.  This test case leverages the BasicInstanceChecks test case. The
        following steps are ran:
            - runs BasicInstanceChecks test case 5 times, 10 second apart.
            - While each test is running, run and terminate instances with a 10sec sleep in between.
            - When a test finishes, rerun BasicInstanceChecks test case.
        If any of these tests fail, the test case will error out; logging the results.
        """
        if self.reservation:
            self.tester.ec2.terminate_instances(self.reservation)
            self.set_reservation(None)

        available_instances_before = self.tester.get_available_vms(zone=self.zone)

        ## Run through count iterations of test
        count = 4
        future_instances =[]

        with ThreadPoolExecutor(max_workers=count) as executor:
            ## Start asynchronous activity
            for i in xrange(count):
                future_instances.append(executor.submit(self.BasicInstanceChecks))
                self.tester.sleep(10)

        with ThreadPoolExecutor(max_workers=count) as executor:
            ## Start asynchronous activity
            ## Terminate all instances
            for future in future_instances:
                executor.submit(self.tester.ec2.terminate_instances,future.result())

        def available_after_greater():
            return self.tester.get_available_vms(zone=self.zone) >= available_instances_before
        self.tester.wait_for_result(available_after_greater, result=True, timeout=360)

    def PrivateIPAddressing(self):
        """
        This case was developed to test instances that are launched with private-addressing
        set to True.  The tests executed are as follows:
            - run an instance with private-addressing set to True
            - allocate/associate/disassociate/release an Elastic IP to that instance
            - check to see if the instance went back to private addressing
        If any of these tests fail, the test case will error out; logging the results.
        """
        if self.reservation:
            for instance in self.reservation.instances:
                if instance.ip_address == instance.private_ip_address:
                    self.tester.info("WARNING: System or Static mode detected, skipping PrivateIPAddressing")
                    return self.reservation
            self.tester.ec2.terminate_instances(self.reservation)
            self.set_reservation(None)
        reservation = self.tester.ec2.run_instance(private_addressing=True, **self.run_instance_params)
        for instance in reservation.instances:
            address = self.tester.ec2.allocate_address()
            self.assertTrue(address,'Unable to allocate address')
            self.tester.ec2.associate_address(instance, address)
            self.tester.sleep(30)
            instance.update()
            self.assertTrue( self.tester.ping(instance.ip_address), "Could not ping instance with new IP")
            address.disassociate()
            self.tester.sleep(30)
            instance.update()
            self.assertFalse(self.tester.ping(instance.ip_address), "Was able to ping instance that should have only had a private IP")
            address.release()
            if instance.ip_address != "0.0.0.0" and instance.ip_address != instance.private_ip_address:
                self.fail("Instance received a new public IP: " + instance.ip_address)
        self.tester.ec2.terminate_instances(self.reservation)
        self.set_reservation(None)
        return reservation

    def ReuseAddresses(self):
        """
        This case was developed to test when you run instances in a series, and make sure
        they get the same address.  The test launches an instance, checks the IP information,
        then terminates the instance. This test is launched 5 times in a row.  If there
        is an error, the test case will error out; logging the results.
        """
        prev_address = None
        if self.reservation:
            self.tester.ec2.terminate_instances(self.reservation)
            self.set_reservation(None)
        for i in xrange(5):
            reservation = self.tester.ec2.run_instance(**self.run_instance_params)
            for instance in reservation.instances:
                if prev_address is not None:
                    self.assertTrue(re.search(str(prev_address) ,str(instance.ip_address)), str(prev_address) +" Address did not get reused but rather  " + str(instance.public_dns_name))
                prev_address = instance.ip_address
            self.tester.ec2.terminate_instances(reservation)

if __name__ == "__main__":
    testcase= EutesterTestCase(name='instancetest')
    testcase.setup_parser(description="Test the Eucalyptus EC2 instance store image functionality.")
    testcase.get_args()
    instancetestsuite= testcase.do_with_args(InstanceBasics)

    ### Either use the list of tests passed from config/command line to determine what subset of tests to run
    list = testcase.args.tests or [ "BasicInstanceChecks", "DNSResolveCheck", "Reboot", "MetaData", "ElasticIps", "MultipleInstances",
                                    "LargestInstance", "PrivateIPAddressing", "Churn"]
    ### Convert test suite methods to EutesterUnitTest objects
    unit_list = []
    for test in list:
        test = getattr(instancetestsuite,test)
        unit_list.append(testcase.create_testunit_from_method(test))
    testcase.clean_method = instancetestsuite.clean_method
    result = testcase.run_test_case_list(unit_list)
    exit(result)
