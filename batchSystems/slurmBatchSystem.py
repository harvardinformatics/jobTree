# -*- coding: utf-8 -*-
"""
Created on Mon Jan  5 14:47:51 2015

@author: akitzmiller
"""

import os
import re

from Queue import Queue,Empty
from threading import Thread

from jobTree.batchSystems.abstractBatchSystem import AbstractBatchSystem
from jobTree.src.master import getParasolResultsFileName
from jobTree.batchSystems.slurm.sbatch import SbatchCommand,Command

import json


sbatch  = Command.fetch("slurm/default/sbatch.json")
sacct   = Command.fetch("slurm/default/sacct.json")
scancel = Command.fetch("slurm/default/scancel.json")

class Worker(Thread):
    def __init__(self, newJobsQueue, updatedJobsQueue, boss):
        Thread.__init__(self)
        self.newJobsQueue = newJobsQueue
        self.updatedJobsQueue = updatedJobsQueue
        self.currentjobs = list()
        self.runningjobs = set()
        self.boss = boss
        
    def run(self):
        while True:
            # Load new job ids:
            while not self.newJobsQueue.empty():
                self.currentjobs.append(self.newJobsQueue.get())

            # Launch jobs as necessary:
            while len(self.currentjobs) > 0:
                jobID, cmdstr = self.currentjobs.pop()
                slurmJobID = slurm(cmdstr)
                self.boss.jobIDs[(slurmJobID, None)] = jobID
                self.boss.slurmJobIDs[jobID] = (slurmJobID, None)
                self.runningjobs.add((slurmJobID, None))

            # Test known job list
            for slurmJobID in list(self.runningjobs):
                exit = getjobexitcode(slurmJobID)
                if exit is not None:
                    self.updatedJobsQueue.put((slurmJobID, exit))
                    self.runningjobs.remove(slurmJobID)

            time.sleep(10)




class SlurmBatchSystem(AbstractBatchSystem):
    """
    BatchSystem adaptation for Slurm
    """
    def __init__(self, config, maxCpus, maxMemory): 
        """
        """
        AbstractBatchSystem.__init__(self, config, maxCpus, maxMemory) #Call the parent constructor
        
        self.resultsFile = getParasolResultsFileName(config.attrib["job_tree"])

        #Reset the job queue and results (initially, we do this again once we've killed the jobs)
        self.resultsFileHandle = open(self.resultsFile, 'w')
        self.resultsFileHandle.close() #We lose any previous state in this file, and ensure the files existence

        self.currentjobs = set()
        self.obtainSystemConstants()
        self.jobIDs = dict()
        self.slurmJobIDs = dict()
        self.nextJobID = 0

        self.newJobsQueue = Queue()
        self.updatedJobsQueue = Queue()
        self.worker = Worker(self.newJobsQueue, self.updatedJobsQueue, self)
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
        """Issues the following command returning a unique jobID. Command
        is the string to run, memory is an int giving
        the number of bytes the job needs to run in and cpu is the number of cpus needed for
        the job and error-file is the path of the file to place any std-err/std-out in.
        """
        jobID = self.nextJobID
        self.nextJobID += 1
        self.currentjobs.add(jobID)
        sbatch.mem = memory
        sbatch.ntasks = cpu
        sbatch.command = command
        sbatch.scriptname = "some script name needs to be set"
        slurmcmd = sbatch.composeCmdString()
        self.newJobsQueue.put((jobID, slurmcmd))
        logger.info("Issued the job command: %s with job id: %s " % (command, str(jobID)))
        return jobID
 
    
    def killJobs(self, jobIDs):
        """
        Kills the given job IDs and makes sure they're dead.
        """
        for jobID in jobIDs:
            slurmJobID = self.getSlurmJobID(jobID)
            logger.info("DEL: " + str(slurmJobID)))
            self.currentjobs.remove(jobID)
            scancel.jobid = slurmJobID
            cmdstr = scancel.composeCmdString()
            process = subprocess.Popen(cmdstr)

            #What is this????
            del self.jobIDs[self.slurmJobIDs[jobID]]
            del self.slurmJobIDs[jobID]

        toKill = set(jobIDs)
        maxattempts = 5
        while len(toKill) > 0:
            for jobID in list(toKill):
                if getjobexitcode(self.lsfJobIDs[jobID]) is not None:
                    toKill.remove(jobID)

            if len(toKill) > 0:
                logger.critical("Tried to kill some jobs, but something happened and they are still going, so I'll try again")
                time.sleep(5)
 
    
    def getIssuedJobIDs(self):
        """
        A list of jobs (as jobIDs) currently issued (may be running, or maybe 
        just waiting).
        """
        return self.currentjobs
    
    def getRunningJobIDs(self):
        """
        Gets a map of jobs (as jobIDs) currently running (not just waiting) 
        and a how long they have been running for (in seconds).
        """
        raise RuntimeError("Abstract method")
    
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
