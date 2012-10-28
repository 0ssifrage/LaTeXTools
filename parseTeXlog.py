import re
import sys
import os.path

print_debug = True
interactive = False
extra_file_ext = []

def debug(s):
	if print_debug:
		print "parseTeXlog: " + s

# If file is not found, ask me if we are debugging
# Rationale: if we are debugging from the command line, perhaps we are parsing
# a log file from a user, so apply heuristics and / or ask if the file not
# found is actually legit
def debug_skip_file(f):
	# If we are not debugging, then it's not a file for sure, so skip it
	if not (print_debug and interactive):
		return True
	debug("debug_skip_file: " + f)
	# Heuristic: TeXlive on Mac or Linux (well, Ubuntu at least) or Windows / MiKTeX
	if ("/usr/local/texlive/" in f) or ("/usr/share/texlive/" in f) or ("Program Files\\MiKTeX" in f):
		print "TeXlive / MiKTeX FILE! Don't skip it!"
		return False
	# Heuristic: no two consecutive spaces in file name
	if "  " in f:
		print "Skip it!"
		return True
	# Heuristic: file in local directory with .tex ending
	file_exts = extra_file_ext + ['tex', 'aux', 'bbl', 'cls', 'sty']
	if f[0:2] in ['./', '.\\', '..'] and os.path.splitext(f)[1].lower()[1:] in file_exts:
		print "File! Don't skip it"
		return False
	if raw_input() == "":
		print "Skip it"
		return True
	else:
		print "FILE! Don't skip it"
		return False


# Log parsing, TNG :-)
# Input: tex log file (decoded), split into lines
# Output: content to be displayed in output panel, split into lines

def parse_tex_log(log):
	debug("Parsing log file")
	errors = []
	warnings = []


	# loop over all log lines; construct error message as needed
	# This will be useful for multi-file documents

	# some regexes
	# file_rx = re.compile(r"\(([^)]+)$") # OLD
	# Structure (+ means captured, - means not captured)
	# + maybe " (for Windows)
	# + maybe a drive letter and : (for Windows)
	# + maybe . NEW: or ../ or ..\, with repetitions
	# + then any char, matched NON-GREEDILY (avoids issues with multiple files on one line?)
	# + then .
	# + then any char except for whitespace or " or ); at least ONE such char
	# + then maybe " (on Windows/MikTeX)
 	# - then whitespace or ), or end of line
 	# + then anything else, captured for recycling
	# This should take care of e.g. "(./test.tex [12" or "(./test.tex (other.tex"
	# NOTES:
	# 1. we capture the initial and ending " if there is one; we'll need to remove it later
	# 2. we define the basic filename parsing regex so we can recycle it
	#file_rx = re.compile(r"\(\"?(\.?[^\.]+\.[^\s\"\)]+)(\s|\"|\)|$)(.*)")
	#file_basic = r"\"?(?:[a-zA-Z]\:)?(?:\.|(?:\.\./)*(?:\.\.\\)*)?[^\.]+\.[^\s\"\)]+"
	file_basic = r"\"?(?:[a-zA-Z]\:)?(?:\.|(?:\.\./)|(?:\.\.\\))*.+?\.[^\s\"\)\.]+\"?"
	file_rx = re.compile(r"\((" + file_basic + r")(\s|\"|\)|$)(.*)")
	# Useless file #1: {filename.ext}; capture subsequent text
	#file_useless1_rx = re.compile(r"\{\"?\.?[^\.]+\.[^\}]*\"?\}(.*)")
	file_useless1_rx = re.compile(r"\{\"?(?:\.|\.\./)*[^\.]+\.[^\}]*\"?\}(.*)")
	# Useless file #2: <filename.ext>; capture subsequent text
	#file_useless2_rx = re.compile(r"<\"?\.?[^\.]+\.[^>]*\"?>(.*)")
	file_useless2_rx = re.compile(r"<\"?(?:\.|\.\./)*[^\.]+\.[^>]*\"?>(.*)")
	pagenum_begin_rx = re.compile(r"\s*\[\d*(.*)")
	line_rx = re.compile(r"^l\.(\d+)\s(.*)")		# l.nn <text>
	warning_rx = re.compile(r"^(.*?) Warning: (.+)") # Warnings, first line
	line_rx_latex_warn = re.compile(r"input line (\d+)\.$") # Warnings, line number
	matched_parens_rx = re.compile(r"\([^()]*\)") # matched parentheses, to be deleted (note: not if nested)
	assignment_rx = re.compile(r"\\[^=]*=")	# assignment, heuristics for line merging

	files = []

	# Support function to handle warnings
	def handle_warning(l):

		if files==[]:
			location = "[no file]"
			errors.append("LaTeXTools cannot correctly detect file names in this LOG file.")
			errors.append("(where: trying to display warning message)")
			errors.append("Please let me know via GitHub (warnings). Thanks!")
		else:
			location = files[-1]		

		warn_match_line = line_rx_latex_warn.search(l)
		if warn_match_line:
			warn_line = warn_match_line.group(1)
			warnings.append(location + ":" + warn_line + ": " + l)
		else:
			warnings.append(location + ": " + l)

	
	# State definitions
	STATE_NORMAL = 0
	STATE_SKIP = 1
	STATE_REPORT_ERROR = 2
	STATE_REPORT_WARNING = 3
	
	state = STATE_NORMAL

	# Use our own iterator instead of for loop
	log_iterator = log.__iter__()
	line_num=0
	line = ""

	recycle_extra = False		# Should we add extra to newly read line?
	reprocess_extra = False		# Should we reprocess extra, without reading a new line?
	emergency_stop = False		# If TeX stopped processing, we can't pop all files

	while True:
		# first of all, see if we have a line to recycle (see heuristic for "l.<nn>" lines)
		if recycle_extra:
			line = extra
			recycle_extra = False
			line_num +=1
		elif reprocess_extra:
			line = extra # NOTE: we must remember that we are reprocessing. See long-line heuristics
		else: # we read a new line
			# save previous line for "! File ended while scanning use of..." message
			prev_line = line
			try:
				line = log_iterator.next() # will fail when no more lines
				line_num += 1
			except StopIteration:
				break
		# Now we deal with TeX's decision to truncate all log lines at 79 characters
		# If we find a line of exactly 79 characters, we add the subsequent line to it, and continue
		# until we find a line of less than 79 characters
		# The problem is that there may be a line of EXACTLY 79 chars. We keep our fingers crossed but also
		# use some heuristics to avoid disastrous consequences
		# We are inspired by latexmk (which has no heuristics, though)

		# HEURISTIC: the first line is always long, and we don't care about it
		# also, the **<file name> line may be long, but we skip it, too (to avoid edge cases)
		# We make sure we are NOT reprocessing a line!!!
		if (not reprocess_extra) and line_num>1 and len(line)>=79 and line[0:2] != "**": 
			# print "Line %d is %d characters long; last char is %s" % (line_num, len(line), line[-1])
			# HEURISTICS HERE
			extend_line = True
			recycle_extra = False
			while extend_line:
				try:
					extra = log_iterator.next()
					line_num += 1 # for debugging purposes
					# HEURISTIC: if extra line begins with "Package:" "File:" "Document Class:",
					# or other "well-known markers",
					# we just had a long file name, so do not add
					if len(extra)>0 and \
					   (extra[0:5]=="File:" or extra[0:8]=="Package:" or extra[0:15]=="Document Class:") or \
					   (extra[0:9]=="LaTeX2e <") or assignment_rx.match(extra):
						extend_line = False
						# no need to recycle extra, as it's nothing we are interested in
					# HEURISTIC: when TeX reports an error, it prints some surrounding text
					# and may use the whole line. Then it prints "...", and "l.<nn> <text>" on a new line
					# If so, do not extend
					elif line[-3:]=="..." and line_rx.match(extra): # a bit inefficient as we match twice
						#print "Found l. <nn> regex"
						extend_line = False
						recycle_extra = True # make sure we process the "l.<nn>" line!
					else:
						line += extra
						if len(extra) < 79:
							extend_line = False
				except StopIteration:
					extend_line = False # end of file, so we must be done. This shouldn't happen, btw
		# We may skip the above "if" because we are reprocessing a line, so reset flag:
		reprocess_extra = False
		# Check various states
		if state==STATE_SKIP:
			state = STATE_NORMAL
			continue
		if state==STATE_REPORT_ERROR:
			# skip everything except "l.<nn> <text>"
			debug(line)
			err_match = line_rx.match(line)
			if not err_match:
				continue
			# now we match!
			state = STATE_NORMAL
			err_line = err_match.group(1)
			err_text = err_match.group(2)
			# err_msg is set from last time
			if files==[]:
				location = "[no file]"
				errors.append("LaTeXTools cannot correctly detect file names in this LOG file.")
				errors.append("(where: trying to display error message)")
				errors.append("Please let me know via GitHub. Thanks!")
			else:
				location = files[-1]		
			errors.append(location + ":" + err_line + ": " + err_msg + " [" + err_text + "]")
			continue
		if state==STATE_REPORT_WARNING:
			# add current line and check if we are done or not
			current_warning += line
			if line[-1]=='.':
				handle_warning(current_warning)
				current_warning = None
				state = STATE_NORMAL # otherwise the state stays at REPORT_WARNING
			continue
		if line=="":
			continue
		# Remove matched parentheses: they do not add new files to the stack
		# Do this iteratatively; for instance, on Windows 64, miktex has some files in
		# "Program Files (x86)", which wreaks havoc
		# NOTE: this means that your file names CANNOT have parentheses!!!
		#
		# NEW: need to rethink this because the new regex deals with (x86) and OTOH this may be bad...
		# REMOVE IT, but add check in file matching for files ending in ")" - so if files are processed immediately,
		# we pop them right away.
		#
		# while True:
		# 	line_purged = matched_parens_rx.sub("", line)
		# 	# if line != line_purged:
		# 		# print "Purged parens on line %d:" % (line_num, )  
		# 		# print line
		# 		# print line_purged
		# 	if line != line_purged:
		# 		line = line_purged
		# 	else:
		# 		break
		# Are we done
		if "Here is how much of TeX's memory you used:" in line:
			if len(files)>0 and (not emergency_stop):
				errors.append("LaTeXTools cannot correctly detect file names in this LOG file.")
				errors.append("(where: finished processing)")
				errors.append("Please let me know via GitHub")
				debug("Done processing, some files left on the stack")
				files=[]			
			break
		# Special error reporting for e.g. \footnote{text NO MATCHING PARENS & co
		if "! File ended while scanning use of" in line:
			scanned_command = line[35:-2] # skip space and period at end
			# we may be unable to report a file by popping it, so HACK HACK HACK
			file_name = log_iterator.next() # <inserted text>
			file_name = log_iterator.next() #      \par
			file_name = log_iterator.next()[3:] # here is the file name with <*> in front
			errors.append("TeX STOPPED: " + line[2:-2]+prev_line[:-5])
			errors.append("TeX reports the error was in file:" + file_name)
			continue
		# Here, make sure there was no uncaught error, in which case we do more special processing
		if "!  ==> Fatal error occurred, no output" in line:
			if errors == []:
				errors.append("TeX STOPPED: fatal errors occurred but LaTeXTools did not see them")
				errors.append("Check the TeX log file, and please let me know via GitHub. Thanks!")
			continue
		if "! Emergency stop." in line:
			state = STATE_SKIP
			emergency_stop = True
			continue
		# catch over/underfull
		# skip everything for now
		# Over/underfull messages end with [] so look for that
		if line[0:8] == "Overfull" or line[0:9] == "Underfull":
			if line[-2:]=="[]": # one-line over/underfull message
				continue
			ou_processing = True
			while ou_processing:
				try:
					line = log_iterator.next() # will fail when no more lines
				except StopIteration:
					debug("Over/underfull: StopIteration (%d)" % line_num)
					break
				line_num += 1
				debug("Over/underfull: skip " + line + " (%d) " % line_num)
				if len(line)>0 and line[0:3] == " []":
					ou_processing = False
			if ou_processing:
				errors.append("Malformed LOG file: over/underfull")
				break
			else:
				continue
		line = line.strip() # get rid of initial spaces
		# note: in the next line, and also when we check for "!", we use the fact that "and" short-circuits
		if len(line)>0 and line[0]==')': # denotes end of processing of current file: pop it from stack
			if files:
				debug(" "*len(files) + files[-1] + " (%d)" % (line_num,))
				files.pop()
				extra = line[1:]
				debug("Reprocessing " + extra)
				reprocess_extra = True
				continue
			else:
				errors.append("LaTeXTools cannot correctly detect file names in this LOG file.")
				errors.append("Please let me know via GitHub. Thanks!")
				debug("Popping inexistent files")
				break
#		line = line.strip() # again, to make sure there is no ") (filename" pattern
		# Opening page indicators: skip and reprocess
		pagenum_begin_match = pagenum_begin_rx.match(line)
		if pagenum_begin_match:
			extra = pagenum_begin_match.group(1)
			debug("Reprocessing " + extra)
			reprocess_extra = True
			continue
		# Closing page indicators: skip and reprocess
		if len(line)>0 and line[0]==']':
			extra = line[1:]
			debug("Reprocessing " + extra)
			reprocess_extra = True
			continue
		# Useless file matches: {filename.ext} or <filename.ext>. We just throw it out
		file_useless_match = file_useless1_rx.match(line) or file_useless2_rx.match(line)
		if file_useless_match: 
			extra = file_useless_match.group(1)
			debug("Useless file: " + line)
			debug("Reprocessing " + extra)
			reprocess_extra = True
			continue
		# this seems to happen often: no need to push / pop it
		if line[:12]=="(pdftex.def)":
			continue
		# Now we should have a candidate file. We still have an issue with lines that
		# look like file names, e.g. "(Font)     blah blah data 2012.10.3" but those will
		# get killed by the isfile call. Not very efficient, but OK in practice
		debug("FILE? Line:" + line)
		file_match = file_rx.match(line)
		if file_match:
			debug("MATCHED")
			file_name = file_match.group(1)
			# remove quotes if necessary
			file_name = file_name.replace("\"", "")
			# This kills off stupid matches
			if (not os.path.isfile(file_name)) and debug_skip_file(file_name):
				continue
			# # remove quotes NO LONGER NEEDED
			# if file_name[0] == "\"" and file_name[-1] == "\"":
			# 	file_name = file_name[1:-1]
			debug("IT'S A FILE!")
			files.append(file_name)
			debug(" "*len(files) + files[-1] + " (%d)" % (line_num,))
			# now we recycle the remainder of this line
			extra = file_match.group(2) + file_match.group(3)
			debug("Reprocessing " + extra)
			reprocess_extra = True
			continue
		if len(line)>0 and line[0]=='!': # Now it's surely an error
			debug(line)
			err_msg = line[2:] # skip "! "
			# next time around, err_msg will be set and we'll extract all info
			state = STATE_REPORT_ERROR
			continue
		warning_match = warning_rx.match(line)
		if warning_match:
			# if last character is a dot, it's a single line
			if line[-1] == '.':
				handle_warning(line)
				continue
			# otherwise, accumulate it
			current_warning = line
			state = STATE_REPORT_WARNING
			continue

	return (errors, warnings)


# If invoked from the command line, parse provided log file

if __name__ == '__main__':
	print_debug = True
	interactive = True
	enc = 'UTF-8' # Should be OK for Linux and OS X, for testing
	try:
		logfilename = sys.argv[1]
		# logfile = open(logfilename, 'r') \
		# 		.read().decode(enc, 'ignore') \
		# 		.encode(enc, 'ignore').splitlines()
		if len(sys.argv) == 3:
			extra_file_ext = sys.argv[2].split(" ")
		logfile = open(logfilename,'r').read().decode(enc,'ignore').splitlines()
		(errors,warnings) = parse_tex_log(logfile)
		print ""
		print "Errors:"
		for err in errors:
			print err
		print ""
		print "Warnings:"
		for warn in warnings:
			print warn

	except Exception, e:
		import traceback
		traceback.print_exc()