"""
Created on Oct 9, 2014
Copyright (c) 2014
Harvard FAS Research Computing
All rights reserved.

@author: Aaron Kitzmiller
"""
import re, os
import tempfile
import logging

SBATCH_NOSUBMIT_OPTIONS =  ['usage','help']

DEFAULT_PDEF_PATH=os.path.join(os.path.dir(__file__),'slurm')

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
    def fetch(cls,name,path=DEFAULT_PDEF_PATH,defaults=None):
        """
        Create a Command object using a JSON definition file.
        defaults is a dict of default values keyed by parameter name.  This can be used to override
        a default value set in the command definition file.
        """
        if not name.endswith('.json'):
            name = name + '.json'
        pardata = {}
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
        for key,pdef in self.parameterdefs:
            self.cmdparametervalues[key] = pdef.default

         
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
                    if pname in self.cmdparametervalues:
                        value = self.cmdparametervalues[pname]
                        
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


    def setParameterDefault(key,value):
        """
        Sets the default value for the named parameter.  Parameter defs must be set.
        """
        pdef = self.getParameterDef(key)
        if pdef is None:
            raise Exception("Unable to set default for %s; Parameter def cannot be found")
        pdef.default = value

         
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
    def __init__(self,scriptpath="./"):
        """
        Set a script path, so that *.sbatch scripts can be written.  Default is cwd.
        """
        self.scriptpath = scriptpath
        
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
            if pname in self.cmdparametervalues:
                value = self.cmdparametervalues[pname]
                
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



DEFAULT_SLURM_CONF_FILE="/etc/slurm/slurm.conf"

class Slurm(object):
    """
    Encapsulation of Slurmy things. Uses Command objects.
    """
    def __init__(self,conffile=DEFAULT_SLURM_CONF_FILE):
        self.loadConfig(conffile)
        self.scancel = Command.fetch("scancel.json")
        self.sbatch  = Command.fetch("sbatch.json")
        self.sacct   = Command.fetch("sacct.json")
    
    def loadConfig(self,conffile):
        '''
        Constructs the object using the given slurm.conf file name
        
        If there are backslashes at the end of the line it's concatenated
        to the next one.
        
        NodeName lines are not saved because of the stupid DEFAULT stuff.  
        Maybe someday.
        '''
        logger.debug("Initializing SlurmConfig using %s" % conffile)
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
                        if 'Partitions' not in self:
                            self['Partitions'] = {}
                        self['Partitions'][pname] = pvalue
                    else:                            
                        self[name] = value
                else:
                    logger.error("Slurm config file %s has strange line '%s'" % (conffile,currline))
                
                currline = ''
    
    def killJob(self,jobid=None):
        pass
    
    def getJobStatus(self,jobid=None):
        pass
    
    def submitJob(self,command,**kwargs):
        pass
    
