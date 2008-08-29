#! /usr/bin/env python

import psycopg2

import time
import datetime
import os
import os.path
import dircache
import shutil
import signal
import sets
import threading
import collections

import logging

logger = logging.getLogger("monitor")

import socorro.lib.util
import socorro.lib.filesystem
import socorro.lib.psycopghelper


class Monitor (object):
  #-----------------------------------------------------------------------------------------------------------------
  def __init__(self, configurationContext):
    super(Monitor, self).__init__()
    self.config = configurationContext
    signal.signal(signal.SIGTERM, Monitor.respondToSIGTERM)
    self.insertionLock = threading.RLock()
    self.quit = False

  #-----------------------------------------------------------------------------------------------------------------
  class NoProcessorsRegisteredException (Exception):
    pass

  #-----------------------------------------------------------------------------------------------------------------
  @staticmethod
  def respondToSIGTERM(signalNumber, frame):
    """ these classes are instrumented to respond to a KeyboardInterrupt by cleanly shutting down.
        This function, when given as a handler to for a SIGTERM event, will make the program respond
        to a SIGTERM as neatly as it responds to ^C.
    """
    logger.info("%s - SIGTERM detected", threading.currentThread().getName())
    raise KeyboardInterrupt

  #-----------------------------------------------------------------------------------------------------------------
  @staticmethod
  def ignoreDuplicateDatabaseInsert (exceptionType, exception, tracebackInfo):
    return exceptionType is psycopg2.IntegrityError

  #-----------------------------------------------------------------------------------------------------------------
  def quitCheck(self):
    if self.quit:
      raise KeyboardInterrupt

  #-----------------------------------------------------------------------------------------------------------------
  def responsiveSleep (self, seconds):
    for x in xrange(int(seconds)):
      self.quitCheck()
      time.sleep(1.0)

  #-----------------------------------------------------------------------------------------------------------------
  def archiveCompletedJobFiles (self, jsonPathname, uuid, newFileExtension):
    logger.debug("%s - archiving %s", threading.currentThread().getName(), jsonPathname)
    newJsonPathname = ("%s/%s%s.%s" % (self.config.saveMinidumpsTo, uuid, self.config.jsonFileSuffix, newFileExtension)).replace('//','/')
    try:
      shutil.move(jsonPathname, newJsonPathname)
    except:
      socorro.lib.util.reportExceptionAndContinue(logger)
    try:
      dumpPathname = "%s%s" % (jsonPathname[:-len(self.config.jsonFileSuffix)], self.config.dumpFileSuffix)
      newDumpPathname = ("%s/%s%s.%s" % (self.config.saveMinidumpsTo, uuid, self.config.dumpFileSuffix, newFileExtension)).replace('//','/')
      logger.debug("%s - archiving %s", threading.currentThread().getName(), dumpPathname)
      shutil.move(dumpPathname, newDumpPathname)
    except:
      socorro.lib.util.reportExceptionAndContinue(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def deleteCompletedJobFiles (self, jsonPathname, unused1, unused2):
    logger.debug("%s - deleting %s", threading.currentThread().getName(), jsonPathname)
    try:
      os.remove(jsonPathname)
    except:
      socorro.lib.util.reportExceptionAndContinue(logger)
    try:
      dumpPathname = "%s%s" % (jsonPathname[:-len(self.config.jsonFileSuffix)], self.config.dumpFileSuffix)
      os.remove(dumpPathname)
    except:
      socorro.lib.util.reportExceptionAndContinue(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def cleanUpCompletedAndFailedJobs (self, databaseConnection, aCursor):
    logger.debug("%s - dealing with completed and failed jobs", threading.currentThread().getName())
    # check the jobs table to and deal with the completed and failed jobs
    try:
      aCursor.execute("select id, pathname, uuid from jobs where success is False")
      fileDisposalFunction = (self.deleteCompletedJobFiles, self.archiveCompletedJobFiles)[self.config.saveFailedMinidumps]
      for jobId, jsonPathname, uuid in aCursor.fetchall():
        self.quitCheck()
        fileDisposalFunction(jsonPathname, uuid, "failed")
        aCursor.execute("delete from jobs where id = %s", (jobId,))
        databaseConnection.commit()
      fileDisposalFunction = (self.deleteCompletedJobFiles, self.archiveCompletedJobFiles)[self.config.saveProcessedMinidumps]
      aCursor.execute("select id, pathname, uuid from jobs where success is True")
      for jobId, jsonPathname, uuid in aCursor.fetchall():
        self.quitCheck()
        fileDisposalFunction(jsonPathname, uuid, "processed")
        aCursor.execute("delete from jobs where id = %s", (jobId,))
        databaseConnection.commit()
    except:
      databaseConnection.rollback()
      socorro.lib.util.reportExceptionAndContinue(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def cleanUpDeadProcessors (self, databaseConnection, aCursor):
    """ look for dead processors - find all the jobs of dead processors and assign them to live processors
        then delete the dead processors
    """
    logger.info("%s - looking for dead processors", threading.currentThread().getName())
    try:
      aCursor.execute("select now() - interval '%s'" % self.config.processorCheckInTime)
      threshold = aCursor.fetchall()[0][0]
      aCursor.execute("select id from processors where lastSeenDateTime < '%s'" % threshold)
      deadProcessors = aCursor.fetchall()
      if deadProcessors:
        logger.info("%s - found dead processor(s):", threading.currentThread().getName())
        for aDeadProcessorTuple in deadProcessors:
          logger.info("%s -   %d is dead", threading.currentThread().getName(), aDeadProcessorTuple[0])
        aCursor.execute("select id from processors where lastSeenDateTime >= '%s'" % threshold)
        liveProcessors = aCursor.fetchall()
        if not liveProcessors:
          raise Monitor.NoProcessorsRegisteredException("There are no processors registered")
        #
        # This code section to reassign jobs from dead processors is blocked because it is very slow
        #
        #numberOfLiveProcessors = len(liveProcessors)
        #aCursor.execute("select count(*) from jobs where owner in (select id from processors where lastSeenDateTime < '%s')" % threshold)
        #numberOfJobsAssignedToDeadProcesors = aCursor.fetchall()[0][0]
        #numberOfJobsPerNewProcessor = numberOfJobsAssignedToDeadProcesors / numberOfLiveProcessors
        #leftOverJobs = numberOfJobsAssignedToDeadProcesors % numberOfLiveProcessors
        #for aLiveProcessorTuple in liveProcessors:
          #aLiveProcessorId = aLiveProcessorTuple[0]
          #logger.info("%s - moving %d jobs from dead processors to procssor #%d", threading.currentThread().getName(), numberOfJobsPerNewProcessor + leftOverJobs, aLiveProcessorId)
          #aCursor.execute("""update jobs set owner = %s, starteddatetime = null where id in
                              #(select id from jobs where owner in
                                #(select id from processors where lastSeenDateTime < %s) limit %s)""", (aLiveProcessorId, threshold, numberOfJobsPerNewProcessor + leftOverJobs))
          #leftOverJobs = 0
        #logger.info("%s - removing all dead processors", threading.currentThread().getName())
        #aCursor.execute("delete from processors where lastSeenDateTime < '%s'" % threshold)
        #databaseConnection.commit()
        ## remove dead processors' priority tables
        #for aDeadProcessorTuple in deadProcessors:
          #try:
            #aCursor.execute("drop table priority_jobs_%d" % aDeadProcessorTuple[0])
            #databaseConnection.commit()
          #except:
            #logger.warning("%s - cannot clean up dead processor in database: the table 'priority_jobs_%d' may need manual deletion", threading.currentThread().getName(), aDeadProcessorTuple[0])
            #databaseConnection.rollback()
    except Monitor.NoProcessorsRegisteredException:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger)
    except:
      socorro.lib.util.reportExceptionAndContinue(logger)

  #-----------------------------------------------------------------------------------------------------------------
  @staticmethod
  def compareSecondOfSequence (x, y):
    return cmp(x[1], y[1])

  #-----------------------------------------------------------------------------------------------------------------
  @staticmethod
  def secondOfSequence(x):
    return x[1]

  #-----------------------------------------------------------------------------------------------------------------
  def jobSchedulerIter(self, aCursor):
    """ This takes a snap shot of the state of the processors as well as the number of jobs assigned to each
        then acts as an iterator that returns a sequence of processor ids.  Order of ids returned will assure that
        jobs are assigned in a balanced manner
    """
    logger.debug("%s - balanced jobSchedulerIter: compiling list of active processors", threading.currentThread().getName())
    try:
      sql = """select p.id, count(j.*) from processors p left join jobs j on p.id = j.owner group by p.id"""
      try:
        aCursor.execute(sql)
        logger.debug("%s - sql succeeded", threading.currentThread().getName())
      except psycopg2.ProgrammingError:
        logger.debug("%s - some other database transaction failed and didn't close properly.  Roll it back and try to continue.", threading.currentThread().getName())
        try:
          aCursor.connection.rollback()
          aCursor.execute(sql)
        except:
          logger.debug("%s - sql failed for the 2nd time - quit", threading.currentThread().getName())
          self.quit = True
          socorro.lib.util.reportExceptionAndAbort(logger)
      listOfProcessorIds = [[aRow[0], aRow[1]] for aRow in aCursor.fetchall()]  #processorId, numberOfAssignedJobs
      if not listOfProcessorIds:
        raise Monitor.NoProcessorsRegisteredException("There are no processors registered")
      while True:
        logger.debug("%s - sort the list of (processorId, numberOfAssignedJobs) pairs", threading.currentThread().getName())
        listOfProcessorIds.sort(Monitor.compareSecondOfSequence)
        # the processor with the fewest jobs is about to be assigned a new job, so increment its count
        listOfProcessorIds[0][1] += 1
        logger.debug("%s - yield the processorId which had the fewest jobs: %d", threading.currentThread().getName(), listOfProcessorIds[0][0])
        yield listOfProcessorIds[0][0]
    except Monitor.NoProcessorsRegisteredException:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def unbalancedJobSchedulerIter(self, aCursor):
    """ This generator returns a sequence of active processorId without regard to job balance
    """
    logger.debug("%s - unbalancedJobSchedulerIter: compiling list of active processors", threading.currentThread().getName())
    try:
      threshold = socorro.lib.psycopghelper.singleValueSql( aCursor, "select now() - interval '%s'" % self.config.processorCheckInTime)
      aCursor.execute("select id from processors where lastSeenDateTime > '%s'" % threshold)
      listOfProcessorIds = [aRow[0] for aRow in aCursor.fetchall()]  #processorId, numberOfAssignedJobs
      if not listOfProcessorIds:
        raise Monitor.NoProcessorsRegisteredException("There are no active processors registered")
      while True:
        for aProcessorId in listOfProcessorIds:
          yield aProcessorId
    except Monitor.NoProcessorsRegisteredException:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def queueJob (self, databaseConnection, databaseCursor, jsonFilePathName, processorIdSequenceGenerator, priority=0):
    aFileName = os.path.basename(jsonFilePathName)
    uuid = aFileName[:-len(self.config.jsonFileSuffix)]
    logger.debug("%s - trying to insert %s", threading.currentThread().getName(), uuid)
    processorIdAssignedToThisJob = processorIdSequenceGenerator.next()
    databaseCursor.execute("insert into jobs (pathname, uuid, owner, priority, queuedDateTime) values (%s, %s, %s, %s, %s)",
                               (jsonFilePathName, uuid, processorIdAssignedToThisJob, priority, datetime.datetime.now()))
    databaseConnection.commit()
    logger.debug("%s - %s assigned to processor %d", threading.currentThread().getName(), uuid, processorIdAssignedToThisJob)
    return processorIdAssignedToThisJob

  #-----------------------------------------------------------------------------------------------------------------
  def queueJobFromSymLink (self, databaseConnection, databaseCursor, jsonFilePathName, symLinkPathname, processorIdSequenceGenerator, priority=0):
    logger.debug("%s - priority %d queuing %s", threading.currentThread().getName(), priority, jsonFilePathName)
    try:
      if not os.path.exists(jsonFilePathName):
        # this is a bad symlink - target missing
        logger.debug("%s - symbolic link: %s is bad. Target missing", threading.currentThread().getName(), symLinkPathname)
        os.unlink(symLinkPathname)
        return
      processorIdAssignedToThisJob = self.queueJob(databaseConnection, databaseCursor, jsonFilePathName, processorIdSequenceGenerator, priority)
      os.unlink(symLinkPathname)
      return processorIdAssignedToThisJob
    except psycopg2.IntegrityError:
      databaseConnection.rollback()
      os.unlink(symLinkPathname)
      logger.debug("%s - %s already in queue - ignoring", threading.currentThread().getName(), uuid)
    except KeyboardInterrupt:
      logger.debug("%s - queueJob detects quit", threading.currentThread().getName())
      self.quit = True
      databaseConnection.rollback()
      raise
    except:
      databaseConnection.rollback()
      socorro.lib.util.reportExceptionAndContinue(logger, logging.ERROR)


  #-----------------------------------------------------------------------------------------------------------------
  def standardJobAllocationLoop(self):
    """
    """
    try:
      self.standardJobAllocationDatabaseConnection = psycopg2.connect(self.config.databaseDSN)
      self.standardJobAllocationCursor = self.standardJobAllocationDatabaseConnection.cursor()
    except:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger) # can't continue without a database connection
    try:
      try:
        while (True):
          self.quitCheck()
          self.cleanUpDeadProcessors(self.standardJobAllocationDatabaseConnection, self.standardJobAllocationCursor)
          self.quitCheck()
          # walk the dump indexes and assign jobs
          logger.debug("%s - getting jobSchedulerIter", threading.currentThread().getName())
          processorIdSequenceGenerator = self.jobSchedulerIter(self.standardJobAllocationCursor)
          logger.debug("%s - beginning index scan", threading.currentThread().getName())
          try:
            for symLinkCurrentDirectory, symLinkName, symLinkPathname in socorro.lib.filesystem.findFileGenerator(os.path.join(self.config.storageRoot, "index"), acceptanceFunction=self.isSymLink):
              logger.debug("%s - looping: %s", threading.currentThread().getName(), symLinkPathname)
              self.quitCheck()
              try:
                logger.debug("%s - found symbolic link: %s", threading.currentThread().getName(), symLinkPathname)
                try:
                  if self.isProperAge(symLinkPathname):
                    relativePathname = os.readlink(symLinkPathname)
                    logger.debug("%s - relative target: %s", threading.currentThread().getName(), relativePathname)
                  else:
                    continue
                except OSError:
                  # this is a bad symlink - target missing?
                  logger.debug("%s - symbolic link: %s is bad. Target missing?", threading.currentThread().getName(), symLinkPathname)
                  os.unlink(symLinkPathname)
                  continue
                absolutePathname = os.path.join(self.config.storageRoot, relativePathname[6:]) # convert relative path to absolute
                logger.debug("%s - index entry found: %s referring to: %s", threading.currentThread().getName(), symLinkPathname, absolutePathname)
                self.quitCheck()
                self.insertionLock.acquire()
                try:
                  self.queueJobFromSymLink(self.standardJobAllocationDatabaseConnection, self.standardJobAllocationCursor, absolutePathname, symLinkPathname, processorIdSequenceGenerator)
                finally:
                  self.insertionLock.release()
              except KeyboardInterrupt:
                logger.debug("%s - inner detects quit", threading.currentThread().getName())
                self.quit = True
                raise
              except:
                socorro.lib.util.reportExceptionAndContinue(logger)
          except:
            socorro.lib.util.reportExceptionAndContinue(logger)
          logger.debug("%s - end of loop - about to sleep", threading.currentThread().getName())
          self.responsiveSleep(self.config.standardLoopDelay)
      except (KeyboardInterrupt, SystemExit):
        logger.debug("%s - outer detects quit", threading.currentThread().getName())
        self.standardJobAllocationDatabaseConnection.rollback()
        self.quit = True
        raise
    finally:
      self.standardJobAllocationDatabaseConnection.close()
      logger.debug("%s - standardLoop done.", threading.currentThread().getName())

  #-----------------------------------------------------------------------------------------------------------------
  def getPriorityUuids(self, aCursor):
    aCursor.execute("select * from priorityJobs")
    dictionaryOfPriorityUuids = {}
    for aUuidRow in aCursor.fetchall():
      dictionaryOfPriorityUuids[aUuidRow[0]] = "%s%s" % (aUuidRow[0], self.config.jsonFileSuffix)
    return dictionaryOfPriorityUuids

  #-----------------------------------------------------------------------------------------------------------------
  def isSymLink(self, testPathTuple):
    logger.debug("%s - testing for symlink: %s", threading.currentThread().getName(), testPathTuple[1])
    return testPathTuple[1].endswith(".symlink")

  #-----------------------------------------------------------------------------------------------------------------
  def isProperAge(self, testPath):
    #logger.debug("%s - %s", threading.currentThread().getName(), testPath)
    #logger.debug("%s - %s", threading.currentThread().getName(), (datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(os.path.getmtime(testPath))) > self.config.minimumSymlinkAge)
    return (datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(os.path.getmtime(testPath))) > self.config.minimumSymlinkAge

  #-----------------------------------------------------------------------------------------------------------------
  def lookForPriorityJobsAlreadyInQueue(self, priorityUuids):
    # check for uuids already in the queue
    for uuid in priorityUuids.keys():
      self.quitCheck()
      try:
        prexistingJobOwner = socorro.lib.psycopghelper.singleValueSql(self.priorityJobAllocationCursor, "select owner from jobs where uuid = '%s'" % uuid)
        logger.info("%s - priority job %s was already in the queue, assigned to %d - raising its priority", threading.currentThread().getName(), uuid, prexistingJobOwner)
        try:
          self.priorityJobAllocationCursor.execute("insert into priority_jobs_%d (uuid) values ('%s')" % (prexistingJobOwner, uuid))
        except psycopg2.ProgrammingError:
          logger.debug("%s - %s assigned to dead processor %d - wait for reassignment", threading.currentThread().getName(), uuid, prexistingJobOwner)
          # likely that the job is assigned to a dead processor
          # skip processing it this time around - by next time hopefully it will have been
          # re assigned to a live processor
          self.priorityJobAllocationDatabaseConnection.rollback()
          del priorityUuids[uuid]
          continue
        self.priorityJobAllocationCursor.execute("update jobs set priority = priority + 1 where uuid = %s", (uuid,))
        self.priorityJobAllocationCursor.execute("delete from priorityJobs where uuid = %s", (uuid,))
        self.priorityJobAllocationDatabaseConnection.commit()
        del priorityUuids[uuid]
      except socorro.lib.psycopghelper.SQLDidNotReturnSingleValue:
        #logger.debug("%s - priority job %s was not already in the queue", threading.currentThread().getName(), uuid)
        pass

  #-----------------------------------------------------------------------------------------------------------------
  def lookForPriorityJobsInSymlinks(self, priorityUuids, processorIdSequenceGenerator, symLinkIndexPath):
    # check for jobs in symlink directories
    for uuid in priorityUuids.keys():
      logger.debug("%s - looking for %s", threading.currentThread().getName(), uuid)
      for path, file, currentDirectory in socorro.lib.filesystem.findFileGenerator(symLinkIndexPath,lambda x: os.path.isdir(x[2])):  # list all directories
        self.quitCheck()
        absoluteSymLinkPathname = os.path.join(currentDirectory, "%s.symlink" % uuid)
        logger.debug("%s -         as %s", threading.currentThread().getName(), absoluteSymLinkPathname)
        try:
          relativeTargetPathname = os.readlink(absoluteSymLinkPathname)
          absoluteTargetPathname = os.path.normpath(os.path.join(currentDirectory, relativeTargetPathname))
        except OSError:
          logger.debug("%s -         Not it...", threading.currentThread().getName())
          continue
        logger.debug("%s -         FOUND", threading.currentThread().getName())
        logger.info("%s - priority queuing %s", threading.currentThread().getName(), absoluteTargetPathname)
        processorIdAssignedToThisJob = self.queueJobFromSymLink(self.priorityJobAllocationDatabaseConnection, self.priorityJobAllocationCursor, absoluteTargetPathname, absoluteSymLinkPathname, processorIdSequenceGenerator, 1)
        logger.info("%s - %s assigned to %d", threading.currentThread().getName(), uuid, processorIdAssignedToThisJob)
        if processorIdAssignedToThisJob:
          self.priorityJobAllocationCursor.execute("insert into priority_jobs_%d (uuid) values ('%s')" % (processorIdAssignedToThisJob, uuid))
        self.priorityJobAllocationCursor.execute("delete from priorityJobs where uuid = %s", (uuid,))
        self.priorityJobAllocationDatabaseConnection.commit()
        del priorityUuids[uuid]
        break

  #-----------------------------------------------------------------------------------------------------------------
  def priorityJobsNotFound(self, priorityUuids):
    # we've failed to find the uuids anywhere
    for uuid in priorityUuids:
      self.quitCheck()
      logger.error("%s - priority uuid %s was never found",  threading.currentThread().getName(), uuid)
      self.priorityJobAllocationCursor.execute("delete from priorityJobs where uuid = %s", (uuid,))
      self.priorityJobAllocationDatabaseConnection.commit()

  #-----------------------------------------------------------------------------------------------------------------
  def priorityJobAllocationLoop(self):
    try:
      self.priorityJobAllocationDatabaseConnection = psycopg2.connect(self.config.databaseDSN)
      self.priorityJobAllocationCursor = self.priorityJobAllocationDatabaseConnection.cursor()
    except:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger) # can't continue without a database connection
    symLinkIndexPath = os.path.join(self.config.storageRoot, "index")
    deferredSymLinkIndexPath = os.path.join(self.config.deferredStorageRoot, "index")
    try:
      try:
        while (True):
          self.quitCheck()
          priorityUuids = self.getPriorityUuids(self.priorityJobAllocationCursor)
          if priorityUuids:
            self.insertionLock.acquire()
            try:
              # assign jobs
              logger.debug("%s - beginning search for priority jobs", threading.currentThread().getName())
              try:
                self.lookForPriorityJobsAlreadyInQueue(priorityUuids)
                if priorityUuids: # only need to continue if we still have jobs to process
                  processorIdSequenceGenerator = self.jobSchedulerIter(self.priorityJobAllocationCursor)
                  self.lookForPriorityJobsInSymlinks(priorityUuids, processorIdSequenceGenerator, symLinkIndexPath)
                  if priorityUuids:
                    self.lookForPriorityJobsInSymlinks(priorityUuids, processorIdSequenceGenerator, deferredSymLinkIndexPath)
                    if priorityUuids:
                      self.priorityJobsNotFound(priorityUuids)
              except KeyboardInterrupt:
                logger.debug("%s - inner detects quit", threading.currentThread().getName())
                raise
              except:
                self.priorityJobAllocationDatabaseConnection.rollback()
                socorro.lib.util.reportExceptionAndContinue(logger)
            finally:
              logger.debug("%s - releasing lock", threading.currentThread().getName())
              self.insertionLock.release()
          logger.debug("%s - sleeping", threading.currentThread().getName())
          self.responsiveSleep(self.config.priorityLoopDelay)
      except (KeyboardInterrupt, SystemExit):
        logger.debug("%s - outer detects quit", threading.currentThread().getName())
        self.priorityJobAllocationDatabaseConnection.rollback()
        self.quit = True
    finally:
      self.priorityJobAllocationDatabaseConnection.close()
      logger.debug("%s - priorityLoop done.", threading.currentThread().getName())

  #-----------------------------------------------------------------------------------------------------------------
  def directoryJudgedDeletable (self, pathname, subDirectoryList, fileList):
    if not (subDirectoryList or fileList) and pathname != self.config.storageRoot: #if both directoryList and fileList are empty
      #select an ageLimit from two options based on the if target directory name has a prefix of "dumpDirPrefix"
      ageLimit = (self.config.dateDirDelta, self.config.dumpDirDelta)[os.path.basename(pathname).startswith(self.config.dumpDirPrefix)]
      logger.debug("%s - agelimit: %s dir age: %s", threading.currentThread().getName(), ageLimit, (datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(os.path.getmtime(pathname))))
      return (datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(os.path.getmtime(pathname))) > ageLimit
    return False

  #-----------------------------------------------------------------------------------------------------------------
  def passJudgementOnDirectory(self, currentDirectory, subDirectoryList, fileList):
    #logger.debug("%s - %s", threading.currentThread().getName(), currentDirectory)
    try:
      if self.directoryJudgedDeletable(currentDirectory, subDirectoryList, fileList):
        logger.debug("%s - removing - %s", threading.currentThread().getName(),  currentDirectory)
        os.rmdir(currentDirectory)
      else:
        logger.debug("%s - not eligible for deletion - %s", threading.currentThread().getName(),  currentDirectory)
    except Exception:
      socorro.lib.util.reportExceptionAndContinue(logger)

  #-----------------------------------------------------------------------------------------------------------------
  def oldDirectoryCleanupLoop (self):
    logger.info("%s - oldDirectoryCleanupLoop starting.", threading.currentThread().getName())
    try:
      try:
        while True:
          logger.info("%s - beginning oldDirectoryCleanupLoop cycle.", threading.currentThread().getName())
          # walk entire tree looking for directories in need of deletion because they're old and empty
          for currentDirectory, directoryList, fileList in os.walk(self.config.storageRoot, topdown=False):
            self.quitCheck()
            self.passJudgementOnDirectory(currentDirectory, directoryList, fileList)
          self.responsiveSleep(self.config.cleanupDirectoryLoopDelay)
      except (KeyboardInterrupt, SystemExit):
        logger.debug("%s - got quit message", threading.currentThread().getName())
        self.quit = True
      except:
        socorro.lib.util.reportExceptionAndContinue(logger)
    finally:
      logger.info("%s - oldDirectoryCleanupLoop done.", threading.currentThread().getName())

  #-----------------------------------------------------------------------------------------------------------------
  def jobCleanupLoop (self):
    logger.info("%s - jobCleanupLoop starting.", threading.currentThread().getName())
    try:
      self.jobCleanupDatabaseConnection = psycopg2.connect(self.config.databaseDSN)
      self.jobCleanupCursor = self.jobCleanupDatabaseConnection.cursor()
    except:
      self.quit = True
      socorro.lib.util.reportExceptionAndAbort(logger) # can't continue without a database connection
    try:
      try:
        while True:
          logger.info("%s - beginning jobCleanupLoop cycle.", threading.currentThread().getName())
          self.cleanUpCompletedAndFailedJobs(self.jobCleanupDatabaseConnection, self.jobCleanupCursor)
          self.responsiveSleep(self.config.cleanupJobsLoopDelay)
      except (KeyboardInterrupt, SystemExit):
        logger.debug("%s - got quit message", threading.currentThread().getName())
        self.quit = True
      except:
        socorro.lib.util.reportExceptionAndContinue(logger)
    finally:
      self.jobCleanupDatabaseConnection.close()
      logger.info("%s - jobCleanupLoop done.", threading.currentThread().getName())

  #-----------------------------------------------------------------------------------------------------------------
  def start (self):
    priorityJobThread = threading.Thread(name="priorityLoopingThread", target=self.priorityJobAllocationLoop)
    priorityJobThread.start()
    jobCleanupThread = threading.Thread(name="jobCleanupThread", target=self.jobCleanupLoop)
    jobCleanupThread.start()
    directoryCleanupThread = threading.Thread(name="directoryCleanupThread", target=self.oldDirectoryCleanupLoop)
    directoryCleanupThread.start()

    try:
      try:
        self.standardJobAllocationLoop()
      finally:
        logger.debug("%s - waiting to join.", threading.currentThread().getName())
        priorityJobThread.join()
        jobCleanupThread.join()
        directoryCleanupThread.join()
    except KeyboardInterrupt:
      raise SystemExit



