"""
Created on Oct 9, 2014
Copyright (c) 2014
Harvard FAS Research Computing
All rights reserved.

@author: Aaron Kitzmiller
"""
import re, os
import tempfile
import json
import subprocess

from sonLib.bioio import logger

SBATCH_NOSUBMIT_OPTIONS =  ['usage','help']

DEFAULT_PDEF_PATH=os.path.join(os.path.dirname(__file__),'slurm')

def getClassFromName(classname):
    """
    Utility that will return the class object for a full qualified 
    classname
    """
    try:
        parts = classname.split('.')
        module = ".".join(parts[:-1])
        m = __import__( module )
        for comp in parts[1:]:
            m = getattr(m, comp)            
        return m
    except ImportError:
        print "Unable to import %s" % classname 



class ParameterDef(object):
    """
    Command parameter definition.  Validation has been removed for jobTree.
    """
    def __init__(self,name=None,required='no',switches=[],pattern=None,description="",validator="command.StringValidator",order=0,default=None):
        if name is None:
            raise Exception("ParameterDef must have a name")
        if pattern is None:
            raise Exception("ParameterDef must have a pattern")
        self.name = name
        self.required = required
        self.switches = switches
        self.pattern = pattern
        self.description = description
        self.order = order
        #validator = getClassFromName(validator)
        #self.validator = validator()
        self.validator = validator
        self.default = default
        
    def isValid(self,value):
        """
        Use specified validator object to check value.  Returns true for jobTree
        """
        #return self.validator.isValid(value)
        return True
        

 
class Command(object):
    """
    Represents a command line.  If composed with ParameterDefs, this can be used to
    validate and interrogate arguments.  It can also be used with a plain string
    command or an array of command elements.
    """
    @classmethod
    def fetch(cls,name,path=DEFAULT_PDEF_PATH,defaults=None,jsonstr=None):
        """
        Create a Command object using a JSON definition file.
        defaults is a dict of default values keyed by parameter name.  This can be used to override
        a default value set in the command definition file.
        """
        if not name.endswith('.json'):
            name = name + '.json'
        pardata = {}
        if jsonstr is not None:
            pardata = json.loads(jsonstr)
        else:
            with open(os.path.join(path,name),'r') as pdeffile:
                pardata = json.load(pdeffile)        
        if pardata is None:
            raise Exception("No command defined in %s" % path)
        if "cmdclass" not in pardata:
            pardata["cmdclass"] = "cmd.Command"
        cls = None
        cls = getClassFromName(pardata["cmdclass"])
        if cls is None:
            raise Exception("Unable to fetch command class %s specified by %s" % (pardata["cmdclass"],name))
        cmd = cls()
        cmd.name        = pardata["name"]
        cmd.bin         = pardata["bin"]
        cmd.version     = pardata["version"]
        cmd.description = pardata["description"]
        
        parameterdefs = []
        for pdef in pardata["parameterdefs"]:
            parameterdefs.append(ParameterDef(**pdef))
        cmd.setParameterDefs(parameterdefs)

        if defaults is not None:
            for pname,default in defaults.iteritems():
                cmd.setParameterDefault(pname,default)
        return cmd
        
    def __init__(self,*args,**kwargs):
        """
        Initialize the command object.  There are multiple modes:
            echo = Command.fetch("/definition/of/echo.json")
            echo.e=True                     #From definition, using parameter switch
             
            parameters = dict("n": "1")
            Command("echo",junkstring,**parameters)  # Dict of parameters  
             
            Command(["echo","junk"])        #An array.  Run without shell interpolation           
                    
        """
         
        #### TODO gotta check for the existence of parameter definitions.  If there 
        #### are definitions, should require keyword args for named parameters
         
        if kwargs is None:
            """
            We have an array of items, probably strings
            """
            if args is None:
                raise Exception("Can't create an empty command")
             
            if len(args) == 1:
                if isinstance(args[0],basestring):
                    """
                    Probably just a single command string
                    """
                    self.cmdstring = args[0]
                elif isinstance(args[0],list):
                    self.cmdarray = args[0]
                else:
                    raise Exception("Single argument Command should be a string or an array")
            else:
                """
                Treat them as an array if they are all strings
                """
                # Make sure they're all strings
                for a in args:
                    if not isinstance(a,basestring):
                        raise Exception("Not sure what you're doing here")
                self.cmdarray = args
        else:
            """
            Some keyword args were passed in
            """
            if len(args) > 0:
                """
                Put them on the command array
                """
                self.cmdarray = args
             
            for k,v in kwargs.iteritems():
                self.setArgValue(k,v)
             
                 
    def reset(self):
        """
        Resets parameter defs to their defaults.
        """
        for key,pdef in self.parameterdefs.iteritems():
            self.setArgValue(key, pdef.default)

         
    def composeCmdString(self):
        """
        Constructs the command string from the various elements.  If the command 
        has arguments they are concatenated first, then key-value pairs are added
        """
        if hasattr(self,"cmdstring"):
            return self.cmdstring
        cmdstring = ""
        if hasattr(self,"cmdarray") and len(self.cmdarray)  > 0:
            cmdstring += " ".join(self.cmdarray)
        if hasattr(self,"cmdparametervalues"):
            if not hasattr(self,"parameterdefs"):

                #
                for k,v in self.cmdparametervalues.iteritems():
                    if not k.startswith("-"):
                        if len(k) == 1:
                            k = "-" + k
                        else:
                            k = "--" + k
                    if v == False:
                        continue
                    if v == True:
                        cmdstring += " %s" % k
                    else:
                        cmdstring += " %s=%s" % (k,v)
            else:
                # This is the branch for commands defined by parameter defs
                # Tool name should be in the "bin" attribute                
                if hasattr(self,"bin"):
                    cmdstring = self.bin
                else:
                    raise Exception("Specified command must have a 'bin' attribute.")
                
                # Determines if the argument pattern is an optional one
                optionalargre = re.compile("\?.+?\?")
                
                # Determines if the argument pattern has quoting of the <VALUE>
                quotecheckre = re.compile("(\S)<VALUE>(\S)")                
                
                # Go through the parameter defs in order and 
                # for any parameter with a value, substitute the value into the 
                # "pattern"
                
                # Sort the parameterdef keys based on pdef.order
                sortednames = sorted(self.parameterdefs.iterkeys(),key=lambda name: int(self.parameterdefs[name].order))
                
                for pname in sortednames:
                    pdef = self.parameterdefs[pname]
                    if pname in self.cmdparametervalues or (hasattr(pdef,'default') and pdef.default is not None):
                
                        value = None
                        if pname in self.cmdparametervalues:
                            value = self.cmdparametervalues[pname]
                        elif hasattr(pdef,'default') and pdef.default is not None and pdef.default != "":
                            value = pdef.default
                        else:
                            continue
                        
                        
                        if value == False:
                            continue
                        
                        # If <VALUE> is surrounded by something (e.g. single quotes)
                        # then we should make sure that char is escaped in the value
                        quotestring = None
                        match = quotecheckre.search(pdef.pattern)
                        if match is not None:
                            if len(match.groups()) == 2:
                                if match.group(1) == match.group(2):
                                    quotestring = match.group(1)
                                    
                        # Do some courtesy escaping
                        if isinstance(value,basestring) and quotestring is not None:
                            # Remove existing escapes
                            value = value.replace("\\" + quotestring,quotestring)
                            # Escape the quote
                            value = value.replace(quotestring,"\\" + quotestring)
                            
                        
                        # Substitute the value into the pattern
                        if optionalargre.search(pdef.pattern) is not None:
                            
                            # This is the case of a switch with an optional argument
                            if value == True:
                                # Adding the switch with no argument
                                cmdstring += " %s" % optionalargre.sub("",pdef.pattern)
                            else:
                                # Remove the question marks and substitute the VALUE
                                cmdstring += " %s" % pdef.pattern.replace("?","").replace("<VALUE>",value)
                                
                        else:
                            if value == True:
                                cmdstring += " %s" % pdef.pattern
                            else:
                                cmdstring += " %s" % pdef.pattern.replace("<VALUE>",value)
                                                    
        return cmdstring.encode('ascii','ignore')
         
     
     
    def setArgValue(self,arg,value):
        """
        Sets arg value, checking against Parameters if they exist
        """
        if not hasattr(self,"cmdparametervalues"):
            self.cmdparametervalues = {}
         
        if hasattr(self,"parameterdefs"):
            if arg not in self.parameterdefs:
                raise Exception("Parameter %s is not valid for this command" % arg)
             
        self.cmdparametervalues[arg] = value 


    def setParameterDefault(self,key,value):
        """
        Sets the default value for the named parameter.  Parameter defs must be set.
        """
        pdef = self.getParameterDef(key)
        if pdef is None:
            raise Exception("Unable to set default for %s; Parameter def cannot be found")
        self.parameterdefs[key].default = value

         
    def getParameterDef(self,key):
        """
        Finds a matching parameter def based on name or switch
        """
        if key in self.parameterdefs:
            return self.parameterdefs[key]
        else:
            for pdef in self.parameterdefs:
                switches = pdef.switches
                if key in switches:
                    return pdef
            return None
        
    
    def setParameterDefs(self,parameterdefs):
        """
        Setup name-keyed parameter list
        """
        self.parameterdefs = {}
        for pdef in parameterdefs:
            self.parameterdefs[pdef.name] = pdef
        
              
    def isValid(self):
        """
        Base class validator just checks parameter validations if 
        Parameter array is set
        """
        if hasattr(self,"parameterdefs"):
            self.validationmsgs = []
            for k,v in self.cmdparametervalues.iteritems():
                msg = self.parameterdefs[k].isValid(v)
                if msg:
                    self.validationmsgs.append(msg)
            if len(self.validationmsgs) > 0:
                return False
            else:
                return True
        else:
            return True
        
    def run(self):
        """
        Run this command using the command string and Popen.  
        A tuple of return code, stdout, and stderr is returned.
        """       
        p = subprocess.Popen(
            self.composeCmdString(),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = p.communicate()
        return [p.returncode,stdout.strip(),stderr.strip()]
        
    def __dir__(self):
        keys = self.__dict__.keys()
        if "parameterdefs" in self.__dict__:
            keys = list(set(keys + self.parameterdefs.keys()))
        return sorted(keys)
        
    
    def __getattr__(self,name):
        if "parameterdefs" in self.__dict__ and name in self.parameterdefs:
            if name in self.cmdparametervalues:
                return self.cmdparametervalues[name]
            else:
                return None
        else:
            return self.__dict__[name]
    
    def __setattr__(self,name,value):
        if "parameterdefs" in self.__dict__ and name in self.parameterdefs:
            self.setArgValue(name, value)
        else:
            self.__dict__[name] = value
            
 





class SbatchCommand(Command):
    """
    Modifications specific to Sbatch, including script generation
    and setting dependencies
    """
    def __init__(self,*args,**kwargs):
        """
        Set a script path, so that *.sbatch scripts can be written.  Default is cwd.
        """
        if "scriptpath" in kwargs:
            self.scriptpath = kwargs["scriptpath"]
            del kwargs["scriptpath"]
        else:
            self.scriptpath = "./"
            
        super(self.__class__,self).__init__(*args,**kwargs)
        
    def composeCmdString(self):
        # If options like --help or --usage are set, use parent for command processing
        for option in SBATCH_NOSUBMIT_OPTIONS:

            if option in self.cmdparametervalues and self.cmdparametervalues[option]:
                return super(self.__class__,self).composeCmdString() 


        cmdstring = "#!/bin/bash\n"
        
        # Determines if the argument pattern is an optional one
        optionalargre = re.compile("\?.+?\?")
        
        # Determines if the argument pattern has quoting of the <VALUE>
        quotecheckre = re.compile("(\S)<VALUE>(\S)")                
        
        # Go through the parameter defs in order and 
        # for any parameter with a value, substitute the value into the 
        # "pattern"
        
        # Sort the parameterdef keys based on pdef.order
        sortednames = sorted(self.parameterdefs.iterkeys(),key=lambda name: int(self.parameterdefs[name].order))
        scriptname = None
        commands = []
        for pname in sortednames:
            pdef = self.parameterdefs[pname]
            if pname in self.cmdparametervalues or (hasattr(pdef,'default') and pdef.default is not None):
                
                value = None
                if pname in self.cmdparametervalues:
                    value = self.cmdparametervalues[pname]
                elif hasattr(pdef,'default'):
                    value = pdef.default
                else:
                    continue
                
                if value == False:
                    continue
                
                # Process scriptname
                if pname == "scriptname":
                    scriptname = value
                    continue
                
                # Process command(s)
                if pname == "command":
                    if isinstance(value,basestring):
                        commands.append(value + "\n")
                    else:
                        if not isinstance(value,list):
                            value = [value]
                        for command in value:
                            if isinstance(command,Command):
                                commands.append("%s\n" % command.composeCmdString())
                            elif isinstance(command,basestring):
                                commands.append(command + "\n")
                            else:
                                raise Exception("Why are you using %s as an sbatch command?" % command.__class__.__name__)
                    continue
                
                # If <VALUE> is surrounded by something (e.g. single quotes)
                # then we should make sure that char is escaped in the value
                quotestring = None
                match = quotecheckre.search(pdef.pattern)
                if match is not None:
                    if len(match.groups()) == 2:
                        if match.group(1) == match.group(2):
                            quotestring = match.group(1)
                            
                # Do some courtesy escaping
                if isinstance(value,basestring) and quotestring is not None:
                    # Remove existing escapes
                    value = value.replace("\\" + quotestring,quotestring)
                    # Escape the quote
                    value = value.replace(quotestring,"\\" + quotestring)
                    
                
                # Substitute the value into the pattern
                if optionalargre.search(pdef.pattern) is not None:
                    
                    # This is the case of a switch with an optional argument
                    if value == True:
                        # Adding the switch with no argument
                        cmdstring += "#SBATCH %s\n" % optionalargre.sub("",pdef.pattern)
                    else:
                        # Remove the question marks and substitute the VALUE
                        cmdstring += "#SBATCH %s\n" % pdef.pattern.replace("?","").replace("<VALUE>",value)
                        
                else:
                    if value == True:
                        cmdstring += "#SBATCH %s\n" % pdef.pattern
                    else:
                        cmdstring += "#SBATCH %s\n" % pdef.pattern.replace("<VALUE>",value)
                   
        cmdstring += "\n".join(commands)
        scriptfile = None                         
        if scriptname is None:
            # Generate a tempfile scriptname
            scriptfile = tempfile.NamedTemporaryFile(mode='w',suffix='.sbatch', dir=self.scriptpath,delete=False)
            scriptname = scriptfile.name
        else:
            if scriptname.startswith("/"):
                scriptfile = open(scriptname,'w')
            else:
                scriptname = os.path.join(self.scriptpath,scriptname)
                scriptfile = open(scriptname,'w')
        scriptfile.write(cmdstring)
        scriptfile.close()
               
        newcmdstring = ' '.join([self.bin,scriptname])
        return newcmdstring.encode('ascii','ignore')

    def __str__(self):
        s = "%s\n" % self.composeCmdString()
        for k,v in self.cmdparametervalues.iteritems():
            s += '-- %s : %s\n' % (k,v)
            
        return s


DEFAULT_SLURM_CONF_FILE="/etc/slurm/slurm.conf"

class Slurm(object):
    """
    Encapsulation of Slurmy things. Uses Command objects populated with truncated 
    ParameterDefs.  Mostly static, stateless methods
    """
    conf = None
    
    sbatchstr = """
{
        "name" : "sbatch",
        "version" : "14.03.8",
        "bin" : "sbatch",
        "description" : "sbatch  submits  a  batch script to SLURM.",
        "cmdclass" : "jobTree.batchSystems.slurm.SbatchCommand",
        "parameterdefs" : [
            {
                "name" : "scriptname",
                "description" : "Name of the script that will be submitted with sbatch",
                "pattern" : "<VALUE>",
                "required" : "yes",
                "order" : "1000"
            },
            {
                "name" : "error",
                "description" : "Instruct SLURM to connect the batch script standard error directly to the file name specified",
                "switches" : ["--error","-e"],
                "pattern" : "--error='<VALUE>'",
                "required" : "no"
            },
            {
                "name" : "job_name",
                "description" : "Instruct SLURM to connect the batch script standard input directly to the file name specified",
                "switches" : ["--job-name","-J"],
                "pattern" : "--job-name='<VALUE>'",
                "required" : "no"
            },
            {
                "name" : "mem",
                "description" : "Specify  the  real memory required per node in MegaBytes.",
                "switches" : ["--mem"],
                "pattern" : "--mem=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "mem_per_cpu",
                "description" : "Mimimum memory required per allocated CPU in MegaBytes.",
                "switches" : ["--mem-per-cpu"],
                "pattern" : "--mem-per-cpu=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "nodes",
                "description" : "Request that a minimum of nodes be allocated to this job.  A maximum node count may also be specified.",
                "switches" : ["--nodes","-N"],
                "pattern" : "--nodes=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "ntasks",
                "description" : "This option  advises  the  SLURM  controller that job steps run within the allocation will launch a maximum of number tasks and to provide for sufficient resources",
                "switches" : ["--ntasks","-n"],
                "pattern" : "--ntasks=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "output",
                "description" : "Instruct  SLURM  to connect the batch script's standard output directly to the file name specified.",
                "switches" : ["--output","-o"],
                "pattern" : "--output='<VALUE>'",
                "required" : "no"
            },
            {
                "name" : "partition",
                "description" : "Open the output and error files using append or truncate mode as specified.",
                "switches" : ["--partition","-p"],
                "pattern" : "--partition=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "time",
                "description" : "Set a limit on the total run time of the job allocation.",
                "switches" : ["--time","-t"],
                "pattern" : "--time=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "tmp",
                "description" : "Specify a minimum amount of temporary disk space (in MB).",
                "switches" : ["--tmp"],
                "pattern" : "--tmp=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "command",
                "description" : "Command to be submitted to Slurm cluster",
                "switches" : [""],
                "pattern" : "<VALUE>",
                "required" : "no",
                "order" : "1000"
            }

         ]
}
    """
    
    scancelstr = """
{
        "name" : "scancel",
        "version" : "14.03.8",
        "bin" : "scancel",
        "description" : "Used to signal jobs or job steps that are under the control of Slurm"       
        "cmdclass" : "jobTree.batchSystems.slurm.Command",
        "parameterdefs" : [
            {
                "name" : "jobid",
                "description" : "The Slurm job ID to be signaled.",
                "pattern" : "<VALUE>",
                "required" : "yes",
                "order" : "100"
            }
         ]
}
    """
    
    sacctcmdstr = """
{
        "name" : "sacct",
        "version" : "14.03.8",
        "bin" : "sacct",
        "description" : "Displays accounting data for all jobs and job steps in the SLURM job accounting log or SLURM database.",
        "cmdclass" : "jobTree.batchSystems.slurm.Command",
        "parameterdefs" : [
            {
                "name" : "jobs",
                "description" : "Displays information about the specified job(.step) or list of job(.step)s.",
                "switches" : ["--jobs","-j"],
                "pattern" : "--jobs='<VALUE>'",
                "required" : "no"
            },
            {
                "name" : "noheader",
                "description" : "No heading will be added to the output. The default action is to display a header.",
                "switches" : ["--noheader","-n"],
                "pattern" : "--noheader",
                "required" : "no"
            },
            {
                "name" : "format",
                "description" : "Comma separated list of fields.",
                "switches" : ["--format","-o"],
                "pattern" : "--format='<VALUE>'",
                "required" : "no"
            }
         ]
}
    """
    
    squeuecmdstr = """
{
        "name" : "squeue",
        "version" : "14.03.8",
        "bin" : "squeue",
        "description" : "squeue is used to view job and job step information for jobs managed by SLURM..",
        "cmdclass" : "jobTree.batchSystems.slurm.Command",
        "parameterdefs" : [
            {
                "name" : "noheader",
                "description" : "Do not print a header on the output",
                "switches" : ["--noheader","-h"],
                "pattern" : "--noheader",
                "required" : "no"
            },
            {
                "name" : "jobs",
                "description" : "Requests a comma separated list of job IDs to display. Defaults to all jobs.",
                "switches" : ["--jobs","-j"],
                "pattern" : "--jobs=<VALUE>",
                "required" : "no"
            },
            {
                "name" : "format",
                "description" : "Specify the information to be displayed, its size and position (right or left justified). Also see the -O <output_format>, --Format=<output_format> option described below (which supports less flexibility in formatting, but supports access to all fields).",
                "switches" : ["--format","-o"],
                "pattern" : "--format='<VALUE>'",
                "required" : "no"
            }
         ]
}    
    """
        
    @classmethod
    def getJobStatus(cls,jobid):
        """
        Uses squeue, then sacct to determine job status.  Status value is 
        returned.
        """
        
        squeue = Command.fetch("squeue",jsonstr=Slurm.squeuecmdstr)
        squeue.reset()
        squeue.jobs = jobid
        squeue.noheader = True
        squeue.format = "%%T"
        [returncode,stdout,stderr] = squeue.run()
        if stdout is not None and stdout.strip() != "":
            logger.info("Status of jobid %s is %s" % (str(jobid),stdout))
            return stdout
        else:
            """
            Try sacct if squeue doesn't return anything
            """
            sacct = Command.fetch("sacct",jsonstr=Slurm.sacctcmdstr)
            sacct.reset()
            sacct.jobs = "%s.batch" % jobid
            sacct.format = "State"
            sacct.noheader = True
            [returncode,stdout,stderr] = sacct.run()
            if returncode != 0:
                raise Exception("sacct failed %s" % sacct.composeCmdString())
            logger.info("Status of jobid %s is %s" % (str(jobid),stdout))
            return stdout

    
    @classmethod
    def getConfigValue(cls,key):
        """
        Get a slurm.conf value.  Calls loadConfig if needed.
        """
        if Slurm.conf is None:
            Slurm.loadConfig()
        if key not in Slurm.conf:
            raise Exception("Slurm config has no key %s" % key)
        return Slurm.conf[key]
        
    
    @classmethod
    def loadConfig(cls,conffile=DEFAULT_SLURM_CONF_FILE):
        '''
        Constructs the object using the given slurm.conf file name.
        If this is called more than once, the values will be reloaded.
        
        If there are backslashes at the end of the line it's concatenated
        to the next one.
        
        NodeName lines are not saved because of the stupid DEFAULT stuff.  
        Maybe someday.
        '''
        logger.debug("Initializing Slurm config using %s" % conffile)
        Slurm.conf = dict()
        currline = ''
        m = re.compile(r'([^=]+)\s*=\s*(.*)') #Used to extract name=value 
        n = re.compile(r'(\S+)\s+(.*)')       #Parse values on PartitionName
        with open(conffile,'rt') as f:
            for line in f:
                line = line.rstrip().lstrip()
                if line.startswith('#') or line.isspace() or not line:
                    continue
            
                # Concatenate lines with escaped line ending
                if line.endswith('\\'):
                    logger.debug("Concatenating line %s" % line)
                    currline += line.rstrip('\\')
                    continue
                
                currline += line
                
                # Skip nodename lines
                if currline.startswith('NodeName'):
                    currline = ''
                    continue
                
                # Split on first equal
                result = m.match(currline)
                if result is not None:
                    name = result.group(1)
                    value = result.group(2)
                    
                    # For PartitionName lines, we need to extract the name 
                    # and add it to the Partitions list
                    if name == 'PartitionName':
                        result2 = n.match(value)
                        if result2 is None:
                            logger.info("Bad PartitionName value %s.  Skipping." \
                                % value)
                            continue
                        pname = result2.group(1)
                        pvalue = result2.group(2)
                        if 'Partitions' not in Slurm.conf:
                            Slurm.conf['Partitions'] = dict()
                        Slurm.conf['Partitions'][pname] = pvalue
                    else:                            
                        Slurm.conf[name] = value
                else:
                    logger.error("Slurm config file %s has strange line '%s'" % (conffile,currline))
                
                currline = ''
    
    @classmethod
    def killJob(cls,jobid):
        """
        Uses scancel to kill the specified job
        """
        scancel = Command.fetch("scancel",jsonstr=Slurm.scancelstr)
        scancel.jobid = jobid
        [returncode,stdout,stderr] = scancel.run()
        if returncode != 0:
            raise Exception("scancel command failed %s" % stderr)
        
    
    @classmethod
    def submitJob(cls,command,**kwargs):
        """
        Uses SbatchCommand to submit a job.  The following environment variables
        are used to set parameters first.
        
        Any slurm parameters that are set as keyword args will override the 
        environment variables.
        """
        sbatch = Command.fetch("sbatch",jsonstr=Slurm.sbatchstr,defaults={"partition":"serial_requeue","time":"100"})
        sbatch.command = command
        envs = {"JT_SLURM_PARTITION":"partition","JT_SLURM_TIME":"time"}
        for env,arg in envs.iteritems():
            if env in os.environ:
                sbatch.setArgValue(arg,os.environ[env])
                
        for arg,value in kwargs.iteritems():
            if arg == "scriptpath":
                sbatch.scriptpath = value
            else:
                sbatch.setArgValue(arg,value)
        logger.info("sbatch command %s" % sbatch)
        
        
        [returncode,stdout,stderr] = sbatch.run()
        
        if returncode != 0:
            raise Exception("sbatch command failed: %s" % stderr)
        
        jobid = stdout.split()[-1]
        return jobid
        
    
