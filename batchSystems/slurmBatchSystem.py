# -*- coding: utf-8 -*-
"""
Created on Mon Jan  5 14:47:51 2015

@author: akitzmiller
"""

import os
import re
import time

from Queue import Queue,Empty
from threading import Thread

from sonLib.bioio import logger

from jobTree.batchSystems.abstractBatchSystem import AbstractBatchSystem
from jobTree.src.master import getParasolResultsFileName
from jobTree.batchSystems.slurm import SbatchCommand,Command,Slurm

import json


class Worker(Thread):
    def __init__(self, newJobsQueue, updatedJobsQueue, boss, slurmopts=dict()):
        Thread.__init__(self)
        self.newJobsQueue = newJobsQueue
        self.updatedJobsQueue = updatedJobsQueue
        self.currentjobs = list()
        self.runningjobs = set()
        self.boss = boss
        self.slurmopts = slurmopts
        
    def run(self):
        while True:
            # Load new job ids:
            while not self.newJobsQueue.empty():
                self.currentjobs.append(self.newJobsQueue.get())

            # Launch jobs as necessary:
            while len(self.currentjobs) > 0:
                jobID, cmdstr, mem, cpu = self.currentjobs.pop()
                
                slurmJobID = Slurm.submitJob(cmdstr,mem=str(int(mem/ 1000000)),ntasks=str(int(cpu)), **self.slurmopts)
                
                self.boss.jobIDs[(slurmJobID, None)] = jobID
                self.boss.slurmJobTasks[jobID] = (slurmJobID, None)
                self.runningjobs.add((slurmJobID, None))

            # Test known job list
            for slurmJobID in list(self.runningjobs):
                exitcode = SlurmBatchSystem.getJobExitCode(slurmJobID)
                if exitcode is not None:
                    self.updatedJobsQueue.put((slurmJobID, exitcode))
                    self.runningjobs.remove(slurmJobID)

            time.sleep(10)


class SlurmBatchSystem(AbstractBatchSystem):
    """
    Batch system class that adapts jobTree for Slurm
    """
    
    @classmethod
    def getDisplayNames(cls):
        """
        Names used to select this batch system.
        """
        return ["slurm"]
    @classmethod
    def getOptionData(cls):
        """
        dictionary used by the option parsing routines to construct command 
        line options for this batch system. 
        
        Example:
        
        opts = {
            "--slurm-partition" : {
                "dest" : "slurm_partition",
                "default" : "general",
                "help" : "Set the partition to be used for normal Slurm batch operations.  Corresponds to sbatch -p/--partition.  The default is 'general'."
            },
            "--slurm-time" : {
                "dest" : "slurm_time",
                "default" : "100",
                "help" : "Set the time limit of the Slurm job.  Corresponds to sbatch -t/--time.  Default is 100 (min)."
            }
        }
                
        """
        opts = {
            "--slurm-partition" : {
                "dest" : "slurm_partition",
                "default" : "general",
                "help" : "Set the partition to be used for normal Slurm batch operations.  Corresponds to sbatch -p/--partition.  The default is 'general'."
            },
            "--slurm-time" : {
                "dest" : "slurm_time",
                "default" : "100",
                "help" : "Set the time limit of the Slurm job.  Corresponds to sbatch -t/--time.  Default is 100 (min)."
            },
            "--slurm-scriptpath" : {
                "dest" : "scriptpath",
                "default" : "./",
                "help" : "Path where sbatch scripts will be stored."
            }
        }
        return opts
    
    @classmethod
    def getJobExitCode(cls,slurmJobTask):
        """
        Returns the exit code for a job.  
        Returns None if the job is pending or if the job cannot be found
        Returns 1 if the job completed but failed
        Returns 0 if the job completed successfully
        """
        
        jobid,task = slurmJobTask
        status = None
        try:
            status = Slurm.getJobStatus(jobid)
        except Exception:
            # Leave it as None
            pass
        
        if status is not None and status.strip() != "":
            if "COMPLETED" in status:
                return 0
            for code in ["PENDING","RUNNING","SUSPENDED","CONFIGURING","COMPLETING","RESIZING"]:
                if code in status:
                    return None
            for code in ["CANCELLED","FAILED","TIMEOUT","PREEMPTED","NODE_FAIL"]:
                if code in status:
                    return 1
                
        return None

    def __init__(self, config, maxCpus, maxMemory): 
        """
        Stuff
        """
        AbstractBatchSystem.__init__(self, config, maxCpus, maxMemory) #Call the parent constructor
        
        self.resultsFile = getParasolResultsFileName(config.attrib["job_tree"])

        #Reset the job queue and results (initially, we do this again once we've killed the jobs)
        self.resultsFileHandle = open(self.resultsFile, 'w')
        self.resultsFileHandle.close() #We lose any previous state in this file, and ensure the files existence

        self.currentjobs = set()
        self.obtainSystemConstants()
        self.jobIDs = dict()
        self.slurmJobTasks = dict()
        self.nextJobID = 0

        self.newJobsQueue = Queue()
        self.updatedJobsQueue = Queue()
        
        # store any of the slurm options
        slurmopts = dict()
        
        optiondata = SlurmBatchSystem.getOptionData()
        for switch, data in optiondata.iteritems():
            key = data['dest']
            if key in config.attrib:
                sbatchopt = re.sub(r'^slurm_','',key)
                slurmopts[sbatchopt] = config.attrib[key]
        self.worker = Worker(self.newJobsQueue, self.updatedJobsQueue, self, slurmopts)
        self.worker.setDaemon(True)
        self.worker.start()
        
        

    def __des__(self):
        #Closes the file handle associated with the results file.
        self.resultsFileHandle.close() 
 
    def checkResourceRequest(self, memory, cpu):
        """Check resource request is not greater than that available.
        """
        assert memory != None
        assert cpu != None
        if cpu > self.maxCpus:
            raise RuntimeError("Requesting more cpus than available. Requested: %s, Available: %s" % (cpu, self.maxCpus))
        if memory > self.maxMemory:
            raise RuntimeError("Requesting more memory than available. Requested: %s, Available: %s" % (memory, self.maxMemory))
    
    def issueJob(self, command, memory, cpu):
        """
        Issues the following command returning a unique jobID by pushing it
        onto the queue used by Worker threads
        Command is the string to run, memory is an int giving
        the number of bytes the job needs to run in and cpu is the number of cpus needed for
        the job.
        """
        jobID = self.nextJobID
        self.nextJobID += 1
        self.currentjobs.add(jobID)
        self.newJobsQueue.put((jobID, command, memory, cpu))
        logger.info("Issued the job command: %s with job id: %s " % (command, str(jobID)))
        return jobID
    

    def getSlurmJobID(self, jobID):
        if not jobID in self.slurmJobTasks:
            RuntimeError("Unknown jobID, could not be converted")

        (jobid,task) = self.slurmJobTasks[jobID]
        if task is None:
            return str(jobid) 
        else:
            return str(jobid) + "." + str(task)   
 
    
    def killJobs(self, jobIDs):
        """
        Kills the given job indexes and makes sure they're dead.
        """
        for jobID in jobIDs:
            slurmJobID = self.getSlurmJobID(jobID)
            logger.info("DEL: " + str(slurmJobID))
            self.currentjobs.remove(jobID)
            try:
                Slurm.killJob(slurmJobID)
            except Exception:
                pass

            #What is this????
            del self.jobIDs[self.slurmJobTasks[jobID]]
            del self.slurmJobTasks[jobID]

        toKill = set(jobIDs)
        maxattempts = 5
        attempts = 0
        while len(toKill) > 0 and attempts < maxattempts:
            for jobID in list(toKill):
                if SlurmBatchSystem.getJobExitCode(self.slurmJobIDs[jobID]) is not None:
                    toKill.remove(jobID)

            if len(toKill) > 0:
                logger.critical("Tried to kill some jobs, but something happened and they are still going, so I'll try again")
                time.sleep(5)
                attempts += 1
 
            
        
        
    def getIssuedJobIDs(self):
        """
        A list of jobs (as jobIDs) currently issued (may be running, or maybe 
        just waiting).
        """
        return self.currentjobs
    
    def getRunningJobIDs(self):
        """
        Gets a map of jobs (as jobIDs) currently running (not just waiting) 
        and a how long they have been running for (in seconds).  Uses Slurm
        squeue
        """
        times = {}
        currentjobs = set(self.slurmJobTasks[x][0] for x in self.getIssuedJobIDs())
        squeue = Command.fetch("squeue",jsonstr=Slurm.squeuecmdstr)
        squeue.reset()
        squeue.jobs = ",".join(currentjobs)
        squeue.format = "'%%i %%T %%S'"
        squeue.noheader = True
        
        [returncode,stdout,stderr] = squeue.run()
        lines = stdout.split("\n")
        for line in lines:
            items = line.strip().split()
            if items[1] == "RUNNING":
                jobstart = time.mktime(time.strptime(items[2],"%Y-%m-%dT%H:%M:%S"))
                times[self.jobIDs[(items[0])]] = time.time() - jobstart
        return times
    
    def getUpdatedJob(self, maxWait):
        """
        Gets a job that has updated its status,
        according to the job manager. Max wait gives the number of seconds to pause 
        waiting for a result. If a result is available returns (jobID, exitValue)
        else it returns None.
        """
        i = None
        try:
            jobID, retcode = self.updatedJobsQueue.get(timeout=maxWait)
            self.updatedJobsQueue.task_done()
            i = (self.jobIDs[jobID], retcode)
            self.currentjobs -= set([self.jobIDs[jobID]])
        except Empty:
            pass

        return i
 
    def getWaitDuration(self):
        """
        We give parasol a second to catch its breath (in seconds)
        """
        #return 0.0
        return 15
    
    def getRescueJobFrequency(self):
        """
        Parasol leaks jobs, but rescuing jobs involves calls to parasol list jobs and pstat2,
        making it expensive. We allow this every 10 minutes..
        """
        return 1800

    def obtainSystemConstants(self):
        """
        This should be able to set self.maxCPU and self.maxMEM
        """
        self.maxCPU = 0
        self.maxMEM = 0

        if self.maxCPU is 0 or self.maxMEM is 0:
                RuntimeError("Can't read ncpus or maxmem info")
        logger.info("Got the maxCPU: %s" % (self.maxMEM))
        
    



def main():
    pass

def _test():
    import doctest      
    return doctest.testmod()

if __name__ == '__main__':
    _test()
    main()
