from __future__ import print_function
from __future__ import division
from collections import deque


class AssemblyGraph(object):
    '''
    This class holds an assembly graph with segments and links.
    '''
    def __init__(self, filename, overlap, paths_file=None):
        self.segments = {} # Dictionary of unsigned segment number -> segment
        self.forward_links = {} # Dictionary of signed segment number -> signed segment number
        self.reverse_links = {} # Dictionary of signed segment number <- signed segment number
        self.copy_depths = {} # Dictionary of unsigned segment number -> list of copy depths
        self.paths = {} # Dictionary of path name -> list of signed segment numbers
        self.overlap = overlap

        if filename.endswith('.fastg'):
            self.load_from_fastg(filename)
        else:
            self.load_from_gfa(filename)

        if paths_file:
            self.load_spades_paths(paths_file)

    def load_from_fastg(self, filename):
        '''
        Loads a Graph from a SPAdes-style FASTG file.
        '''
        # Load in the graph segments.
        headers, sequences = get_headers_and_sequences(filename)
        for i, header in enumerate(headers):
            num = get_unsigned_number_from_header(header)
            sequence = sequences[i]
            positive = is_header_positive(header)

            # If the segment already exists, then add this sequence.
            if num in self.segments:
                self.segments[num].add_sequence(sequence, positive)

            # If the segment does not exist, make it.
            else:
                depth = get_depth_from_header(header)
                segment = Segment(num, depth, sequence, positive)
                self.segments[num] = segment

        # Make sure that every segment has both a forward and reverse sequence.
        for segment in self.segments.itervalues():
            segment.build_other_sequence_if_necessary()

        # Load in the links.
        for header in headers:
            start, end_list = get_links_from_header(header)
            if end_list:
                self.forward_links[start] = end_list
        self.forward_links = build_rc_links_if_necessary(self.forward_links)
        self.reverse_links = build_reverse_links(self.forward_links)

    def load_from_gfa(self, filename):   
        '''
        Loads a Graph from a GFA file. It does not load any GFA file, but makes some restrictions:
        1) The segment names must be integers.
        2) The depths should be stored in a DP tag.
        3) All link overlaps are the same (equal to the graph overlap value).
        '''
        # Load in the segments.
        gfa_file = open(filename, 'r')
        for line in gfa_file:
            if line.startswith('S'):
                line_parts = line.strip().split('\t')
                num = int(line_parts[1])
                depth = 1.0
                for part in line_parts:
                    if part.startswith('DP:'):
                        depth = float(part[5:])
                sequence = line_parts[2]
                self.segments[num] = Segment(num, depth, sequence, True)
                self.segments[num].build_other_sequence_if_necessary()
        gfa_file.close()

        # Load in the links.
        gfa_file = open(filename, 'r')
        for line in gfa_file:
            if line.startswith('L'):
                line_parts = line.strip().split('\t')
                start = signed_string_to_int(line_parts[1] + line_parts[2])
                end = signed_string_to_int(line_parts[3] + line_parts[4])
                if start not in self.forward_links:
                    self.forward_links[start] = [end]
                else:
                    self.forward_links[start].append(end)
        self.forward_links = build_rc_links_if_necessary(self.forward_links)
        self.reverse_links = build_reverse_links(self.forward_links)
        gfa_file.close()

        # Load in the paths
        gfa_file = open(filename, 'r')
        for line in gfa_file:
            if line.startswith('P'):
                line_parts = line.strip().split('\t')
                path_name = line_parts[1]
                segments = [signed_string_to_int(x) for x in line_parts[2].split(',')]
                self.paths[path_name] = segments
        gfa_file.close()

    def load_spades_paths(self, filename):
        '''
        Loads in SPAdes contig paths from file.
        It only saves the positive paths and does not save paths with only one segment.
        If a SPAdes path has a gap (semicolon), then it treats each component part as a separate
        path (i.e. paths do not span gaps).
        '''
        names = []
        segment_strings = []
        name = ''
        segment_string = ''

        paths_file = open(filename, 'r')
        for line in paths_file:
            line = line.strip()
            if not line:
                continue
            if line.startswith('NODE'):
                if name:
                    names.append(name)
                    segment_strings.append(segment_string)
                    name = ''
                    segment_string = ''
                name = line
            else:
                segment_string += line
        paths_file.close()
        if name:
            names.append(name)
            segment_strings.append(segment_string)

        for i, name in enumerate(names):
            if name.endswith("'"):
                continue
            name_parts = name.split('_')
            if len(name_parts) < 2:
                continue
            name = '_'.join(name_parts[:2])
            segment_string = segment_strings[i]
            if not segment_string:
                continue
            segment_string_parts = segment_string.split(';')
            segment_string_parts = [x for x in segment_string_parts if len(x.split(',')) > 1]
            for j, segment_string_part in enumerate(segment_string_parts):
                path_name = name
                if len(segment_string_parts) > 1:
                    path_name += '_' + str(j+1)
                segments = [signed_string_to_int(x) for x in segment_string_part.split(',')]
                self.paths[path_name] = segments

    def get_median_read_depth(self, segment_list=None):
        '''
        Returns the assembly graph's median read depth (by base).  Optionally, a list of segments
        can be given, in which case only those segments are used for the calculation.
        '''
        if not segment_list:
            segment_list = self.segments.values()
        sorted_segments = sorted(segment_list, key=lambda x: x.depth)
        total_length = 0
        for segment in sorted_segments:
            total_length += segment.get_length_no_overlap(self.overlap)
        halfway_length = total_length // 2
        length_so_far = 0
        for segment in sorted_segments:
            length_so_far += segment.get_length_no_overlap(self.overlap)
            if length_so_far >= halfway_length:
                return segment.depth
        return 0.0

    def normalise_read_depths(self):
        '''
        For every segment in the graph, divide its depth by the graph's median.
        This makes segments with the median depth have a depth of 1, segments with more than the
        median a depth of greater than 1 and segments with less than the median a depth of less
        than 1.
        '''
        median_depth = self.get_median_read_depth()
        for segment in self.segments.itervalues():
            segment.divide_depth(median_depth)

    def get_total_length(self):
        '''
        Returns the sum of all segment sequence lengths.
        '''
        total_length = 0
        for segment in self.segments.itervalues():
            total_length += segment.get_length()
        return total_length

    def get_total_length_no_overlaps(self):
        '''
        Returns the sum of all segment sequence lengths, subtracting the overlap size from each
        node.
        '''
        total_length = 0
        for segment in self.segments.itervalues():
            total_length += segment.get_length_no_overlap(self.overlap)
        return total_length

    def save_to_fasta(self, filename):
        fasta = open(filename, 'w')
        sorted_segments = sorted(self.segments.values(), key=lambda x: x.number)
        for segment in sorted_segments:
            fasta.write('>' + str(segment.number) + '\n')
            fasta.write(add_line_breaks_to_sequence(segment.forward_sequence, 60))

    def save_to_fastg(self, filename):
        fastg = open(filename, 'w')
        sorted_segments = sorted(self.segments.values(), key=lambda x: x.number)
        for segment in sorted_segments:
            fastg.write(self.get_fastg_header_with_links(segment, True))
            fastg.write(add_line_breaks_to_sequence(segment.forward_sequence, 60))
            fastg.write(self.get_fastg_header_with_links(segment, False))
            fastg.write(add_line_breaks_to_sequence(segment.reverse_sequence, 60))

    def save_to_gfa(self, filename):
        gfa = open(filename, 'w')
        sorted_segments = sorted(self.segments.values(), key=lambda x: x.number)
        for segment in sorted_segments:
            segment_line = segment.gfa_segment_line()
            if segment.number in self.copy_depths:
                segment_line = segment_line[:-1] # Remove newline
                segment_line += '\tLB:z:' + self.get_depth_string(segment)
                segment_line += '\tCL:z:' + self.get_copy_number_colour(segment)
                segment_line += '\n'
            gfa.write(segment_line)
        gfa.write(self.get_all_gfa_link_lines())
        paths = sorted(self.paths.items())
        overlap_cigar = str(self.overlap) + 'M'
        for path_name, segment_list in paths:
            gfa.write('P\t' + path_name + '\t')
            gfa.write(','.join([int_to_signed_string(x) for x in segment_list]))
            gfa.write('\t')
            gfa.write(','.join([overlap_cigar] * (len(segment_list) - 1)))
            gfa.write('\n')
        gfa.close()

    def get_all_gfa_link_lines(self):
        gfa_link_lines = ''
        for start, ends in self.forward_links.iteritems():
            for end in ends:
                if is_link_positive(start, end):
                    gfa_link_lines += self.gfa_link_line(start, end)
        return gfa_link_lines

    def get_fastg_header_with_links(self, segment, positive):
        '''
        Returns a full SPAdes-style FASTG header for a segment, including the leading '>', all of
        the links, the trailing ';' and a newline.
        '''
        number = segment.number
        if not positive:
            number *= -1
        header = '>' + segment.get_fastg_header(positive)
        if number in self.forward_links:
            header += ':'
            next_segment_headers = []
            for next_num in self.forward_links[number]:
                if next_num < 0:
                    next_positive = False
                    next_num *= -1
                else:
                    next_positive = True
                next_segment = self.segments[next_num]
                next_segment_headers.append(next_segment.get_fastg_header(next_positive))
            header += ','.join(next_segment_headers)
        header += ';\n'
        return header

    def total_dead_end_count(self):
        '''
        Returns the total number of dead ends in the assembly graph.
        '''
        dead_ends = 0
        for segment in self.segments.itervalues():
            dead_ends += self.dead_end_count(segment)
        return dead_ends

    def dead_end_count(self, segment):
        '''
        Returns the number of dead ends for one segment: 0, 1 or 2.
        '''
        segment_num = segment.number
        dead_ends = 0
        if segment_num not in self.forward_links:
            dead_ends += 1
        if segment_num not in self.reverse_links:
            dead_ends += 1
        return dead_ends

    def filter_by_read_depth(self, relative_depth_cutoff):
        '''
        This function removes segments from the graph based on a relative depth cutoff. Segments
        are considered below the cutoff if they are less than the cutoff for the entire graph or
        less than the cutoff for their connected component.
        To be removed, one of the following must also be true:
          1) the segment has at least one dead end
          2) the segment is part of a connected component where all of the segments are below the
             whole graph cutoff
          3) deleting the segment would not create any dead ends
        '''
        segment_nums_to_remove = []
        whole_graph_cutoff = self.get_median_read_depth() * relative_depth_cutoff
        connected_components = self.get_connected_components()
        for component in connected_components:
            component_segs = [self.segments[x] for x in component]
            component_cutoff = self.get_median_read_depth(component_segs) * relative_depth_cutoff
            for num in component:
                segment = self.segments[num]
                if segment.depth < whole_graph_cutoff or segment.depth < component_cutoff:
                    if self.dead_end_count(segment) > 0 or \
                       self.all_segments_below_depth(component, whole_graph_cutoff) or \
                       not self.deleting_would_create_dead_end(segment):
                        segment_nums_to_remove.append(num)
        self.remove_segments(segment_nums_to_remove)

    def filter_homopolymer_loops(self):
        '''
        A common feature in SPAdes graphs is a small piece of the graph (often just one node) which
        has nothing but one base.  Filter these out.
        '''
        segment_nums_to_remove = []
        connected_components = self.get_connected_components()
        for component_nums in connected_components:
            component_segments = [self.segments[x] for x in component_nums]
            if all_segments_are_one_base(component_segments):
                segment_nums_to_remove += component_nums
        self.remove_segments(segment_nums_to_remove)

    def remove_segments(self, nums_to_remove):
        '''
        Given a list of segment numbers to remove, this function rebuilds the graph's segments
        and links, excluding those segments. It also deletes any paths which contain those
        segments.
        '''
        new_segments = {}
        for num, segment in self.segments.iteritems():
            if num not in nums_to_remove:
                new_segments[num] = segment
        self.segments = new_segments

        self.forward_links = remove_nums_from_links(self.forward_links, nums_to_remove)
        self.reverse_links = remove_nums_from_links(self.reverse_links, nums_to_remove)

        paths_to_delete = set()
        neg_nums_to_remove = [-x for x in nums_to_remove]
        for path_name, path_nums in self.paths.iteritems():
            if len(list(set(nums_to_remove) & set(path_nums))) > 0:
                paths_to_delete.add(path_name)
            if len(list(set(neg_nums_to_remove) & set(path_nums))) > 0:
                paths_to_delete.add(path_name)
        for path_to_delete in paths_to_delete:
            del self.paths[path_to_delete]

    def merge_all_possible(self):
        '''
        This function merges nodes which are in a simple, unbranching path.
        '''
        try_to_merge = True
        while try_to_merge:
            try_to_merge = False
            for num, _ in self.segments.iteritems():
                if num in self.forward_links and len(self.forward_links[num]) == 1:
                    merged = self.try_to_merge_two_segments(num, self.forward_links[num][0])
                    if merged:
                        try_to_merge = True
                        break
                if -num in self.forward_links and len(self.forward_links[-num]) == 1:
                    merged = self.try_to_merge_two_segments(-num, self.forward_links[-num][0])
                    if merged:
                        try_to_merge = True
                        break

    def try_to_merge_two_segments(self, seg_num_1, seg_num_2):
        '''
        If seg_1 and seg_2 can be merged, this function does so and returns True. Otherwise it
        returns False.
        '''
        if seg_num_1 == seg_num_2:
            return False
        if seg_num_1 not in self.forward_links or seg_num_2 not in self.reverse_links:
            return False
        if len(self.forward_links[seg_num_1]) != 1 or len(self.reverse_links[seg_num_2]) != 1:
            return False
        if self.forward_links[seg_num_1][0] != seg_num_2 or \
           self.reverse_links[seg_num_2][0] != seg_num_1:
            return False
        self.merge_two_segments(seg_num_1, seg_num_2)
        return True


    def merge_two_segments(self, seg_num_1, seg_num_2):
        '''
        Merges seg_1 and seg_2 into a single segment and adjusts any paths as necessary. Assumes
        that seg_1 and seg_2 form a simple, unbranching path and can be merged.
        '''
        seg_1 = self.segments[abs(seg_num_1)]
        seg_2 = self.segments[abs(seg_num_2)]

        seg_1_len = seg_1.get_length()
        seg_2_len = seg_2.get_length()

        seg_1_seq = self.get_seq_from_signed_seg_num(seg_num_1)
        seg_2_seq = self.get_seq_from_signed_seg_num(seg_num_2)

        merged_forward_seq = seg_1_seq[:-self.overlap] + seg_2_seq
        merged_reverse_seq = reverse_complement(merged_forward_seq)

        # The merged sequence depth is the weighted mean of the two components.
        seg_1_len = seg_1.get_length() - self.overlap
        seg_2_len = seg_2.get_length() - self.overlap
        len_sum = seg_1_len + seg_2_len
        if len_sum > 0.0:
            mean_depth = seg_1.depth * (seg_1_len / len_sum) + seg_2.depth * (seg_2_len / len_sum)
        else:
            mean_depth = 1.0

        # Create a new merged segment using the number of whichever component segment is larger.
        new_seg_num = self.get_next_available_seg_number()
        if seg_2_len > seg_1_len:
            new_seg_num = abs(seg_num_2)
        new_seg = Segment(new_seg_num, mean_depth, merged_forward_seq, True)
        new_seg.reverse_sequence = merged_reverse_seq

        # Save some info that we'll need, and then delete the old segments.
        paths_copy = self.paths.copy()
        outgoing_links = []
        if seg_num_2 in self.forward_links:
            outgoing_links = self.forward_links[seg_num_2]
        incoming_links = []
        if seg_num_1 in self.reverse_links:
            incoming_links = self.reverse_links[seg_num_1]
        self.remove_segments([abs(seg_num_1), abs(seg_num_2)])

        # Add the new segment to the graph and give it the links from its source segments.
        self.segments[new_seg_num] = new_seg
        for link in outgoing_links:
            self.add_link(new_seg_num, link)
        for link in incoming_links:
            self.add_link(link, new_seg_num)

        # Merge the segments in any paths.
        for path_name in paths_copy.iterkeys():
            paths_copy[path_name] = find_replace_in_list(paths_copy[path_name],
                                                         [seg_num_1, seg_num_2], new_seg_num)
            paths_copy[path_name] = find_replace_in_list(paths_copy[path_name],
                                                         [-seg_num_2, -seg_num_1], -new_seg_num)

        # If any paths still contain the original segments, then split those paths into pieces,
        # removing the original segments.
        new_paths = {}
        for path_name, path_segments in paths_copy.iteritems():
            split_paths = split_path_multiple(path_segments, [seg_num_1, seg_num_2,
                                                              -seg_num_1, -seg_num_2])
            if len(split_paths) == 1:
                new_paths[path_name] = split_paths[0]
            elif len(split_paths) > 1:
                for i, path in enumerate(split_paths):
                    new_paths[path_name+'_'+str(i+1)] = path
        self.paths = new_paths





    def add_link(self, start, end):
        '''
        Adds a link to the graph in all necessary ways: forward and reverse, and for reverse
        complements too.
        '''
        if start not in self.forward_links:
            self.forward_links[start] = []
        if end not in self.forward_links[start]:
            self.forward_links[start].append(end)

        if end not in self.reverse_links:
            self.reverse_links[end] = []
        if start not in self.reverse_links[end]:
            self.reverse_links[end].append(start)

        if -start not in self.reverse_links:
            self.reverse_links[-start] = []
        if -end not in self.reverse_links[-start]:
            self.reverse_links[-start].append(-end)

        if -end not in self.forward_links:
            self.forward_links[-end] = []
        if -start not in self.forward_links[-end]:
            self.forward_links[-end].append(-start)

    def get_seq_from_signed_seg_num(self, signed_num):
        '''
        Returns the forwards or reverse sequence of a segment, if the number is next_positive or
        negative, respectively. Assumes the segment number is in the graph.
        '''
        if signed_num > 0:
            return self.segments[signed_num].forward_sequence
        else:
            return self.segments[-signed_num].reverse_sequence

    def get_connected_components(self):
        '''
        Returns a list of lists, where each inner list is the segment numbers of one connected
        component of the graph.
        E.g. [[1, 2], [3, 4, 5]] would mean that segments 1 and 2 are in a connected component
        and segments 3, 4 and 5 are in another connected component. 
        '''
        visited = set()
        components = []
        for v in self.segments.iterkeys():
            if v not in visited:
                component = []
                q = deque()
                q.append(v)
                visited.add(v)
                while q:
                    w = q.popleft()
                    component.append(w)
                    connected_segments = self.get_connected_segments(w)
                    for k in connected_segments:
                        if k not in visited:
                            visited.add(k)
                            q.append(k)
                components.append(component)
        return components

    def get_connected_segments(self, segment_num):
        '''
        Given a segment number, this function returns a list of all other segment numbers for
        segments that are directly connected.
        It only returns positive numbers (i.e. is not strand-specific).
        '''
        connected_segments = set()
        if segment_num in self.forward_links:
            downstream_segments = self.forward_links[segment_num]
            for segment in downstream_segments:
                connected_segments.add(abs(segment))
        if segment_num in self.reverse_links:
            upstream_segments = self.reverse_links[segment_num]
            for segment in upstream_segments:
                connected_segments.add(abs(segment))
        return list(connected_segments)

    def all_segments_below_depth(self, segment_nums, cutoff):
        '''
        Returns true if all segments in the list are below the depth cutoff.
        '''
        for num in segment_nums:
            if self.segments[num].depth >= cutoff:
                return False
        return True

    def get_n_segment_length(self, n_percent):
        '''
        Returns the length for which segments that length and longer make up >= n% of the total
        bases.  E.g. if n = 50, this function returns the N50.  n must be from 0 to 100.
        '''
        total_length = self.get_total_length_no_overlaps()
        target_length = total_length * (n_percent / 100.0)
        sorted_segments = sorted(self.segments.values(),
                                 key=lambda x: x.get_length_no_overlap(self.overlap),
                                 reverse=True)
        length_so_far = 0
        for segment in sorted_segments:
            seg_length = segment.get_length_no_overlap(self.overlap)
            length_so_far += seg_length
            if length_so_far >= target_length:
                return seg_length
        return 0

    def gfa_link_line(self, start, end):
        '''
        Returns an entire L line for GFA output, including the newline.
        '''
        l_line = 'L\t'
        l_line += str(abs(start)) + '\t'
        l_line += get_sign_string(start) + '\t'
        l_line += str(abs(end)) + '\t'
        l_line += get_sign_string(end) + '\t'
        l_line += str(self.overlap) + 'M\n'
        return l_line

    def get_all_outputs(self, segment):
        '''
        Returns a list of segments which lead out from the given segment.
        '''
        if segment.number in self.reverse_links:
            return [self.segments[x] for x in self.forward_links[segment.number]]
        else:
            return []

    def get_exclusive_inputs(self, segment_number):
        '''
        This function finds all segments which lead into the given segment.  If those segments
        do not lead into any other segments, then this function returns them in a list.  If they
        do lead into other segments, then this function returns None.
        Specifically, this function returns a list of unsigned numbers.
        '''
        if segment_number not in self.reverse_links:
            return []
        return [abs(x) for x in self.reverse_links[segment_number] if self.lead_exclusively_to(x, segment_number)]

    def get_exclusive_outputs(self, segment_number):
        '''
        Does the same thing as get_exclusive_inputs, but in the other direction.
        '''
        if segment_number not in self.forward_links:
            return []
        return [abs(x) for x in self.forward_links[segment_number] if self.lead_exclusively_from(x, segment_number)]

    def lead_exclusively_to(self, segment_num_1, segment_num_2):
        '''
        Returns whether or not the first segment leads to and only to the second segment.
        '''
        if segment_num_1 not in self.forward_links:
            return False
        return self.forward_links[segment_num_1] == [segment_num_2]

    def lead_exclusively_from(self, segment_num_1, segment_num_2):
        '''
        Does the same thing as lead_exclusively_to, but follows links in the opposite direction.
        '''
        if segment_num_1 not in self.reverse_links:
            return False
        return self.reverse_links[segment_num_1] == [segment_num_2]

    def deleting_would_create_dead_end(self, segment):
        '''
        If deleting the given segment would create a dead end, this function returns True.
        '''
        downstream_segments = self.forward_links[segment.number]
        for downstream_segment in downstream_segments:
            if len(self.reverse_links[downstream_segment]) == 1:
                return True
        upstream_segments = self.reverse_links[segment.number]
        for upstream_segment in upstream_segments:
            if len(self.forward_links[upstream_segment]) == 1:
                return True
        return False

    def clean(self, read_depth_filter):
        '''
        This function does various graph repairs, filters and normalisations to make it a bit
        nicer.
        '''
        self.repair_four_way_junctions()
        self.filter_by_read_depth(read_depth_filter)
        self.filter_homopolymer_loops()
        self.merge_all_possible()
        self.normalise_read_depths()

    def repair_four_way_junctions(self):
        '''
        This function finds and fixes four-way junctions in the graph, as these can mess up copy
        number determination. It fixes them by creating a new node with no length (i.e with the
        overlap size) to bridge the connection.
        For example: A->B,C and D->B,C becomes A->E and D->E and E->B and E->C
        '''
        seg_nums = self.segments.keys()
        seg_nums += [-x for x in self.segments.keys()]
        for seg_num in seg_nums:
            if seg_num not in self.forward_links:
                continue
            ending_segs = self.forward_links[seg_num]
            if len(ending_segs) != 2:
                continue
            end_num_1 = ending_segs[0]
            end_num_2 = ending_segs[1]
            if len(self.reverse_links[end_num_1]) != 2 or \
               len(self.reverse_links[end_num_2]) != 2:
                continue
            starting_segs = set(self.reverse_links[end_num_1])
            starting_segs.union(set(self.reverse_links[end_num_2]))
            starting_segs = list(starting_segs)
            if len(starting_segs) != 2:
                continue

            # If the code got here, then we've found a four-way junction! Create a new segment
            # to bridge the starting and ending segments.
            start_num_1 = starting_segs[0]
            start_num_2 = starting_segs[1]
            start_1 = self.segments[abs(start_num_1)]
            start_2 = self.segments[abs(start_num_2)]
            end_1 = self.segments[abs(end_num_1)]
            end_2 = self.segments[abs(end_num_2)]
            if end_1 > 0:
                bridge_seq = end_1.forward_sequence[:self.overlap]
            else:
                bridge_seq = end_1.reverse_sequence[:self.overlap]
            bridge_depth = (start_1.depth + start_2.depth + end_1.depth + end_2.depth) / 2.0
            bridge_num = self.get_next_available_seg_number()
            bridge_seg = Segment(bridge_num, bridge_depth, bridge_seq, True)
            bridge_seg.build_other_sequence_if_necessary()
            self.segments[bridge_num] = bridge_seg

            # Now rebuild the links around the junction.
            self.forward_links[start_num_1] = [bridge_num]
            self.forward_links[start_num_2] = [bridge_num]
            self.forward_links[bridge_num] = [end_num_1, end_num_2]
            self.reverse_links[bridge_num] = [start_num_1, start_num_2]
            self.reverse_links[end_num_1] = [bridge_num]
            self.reverse_links[end_num_2] = [bridge_num]
            self.reverse_links[-start_num_1] = [-bridge_num]
            self.reverse_links[-start_num_2] = [-bridge_num]
            self.reverse_links[-bridge_num] = [-end_num_1, -end_num_2]
            self.forward_links[-bridge_num] = [-start_num_1, -start_num_2]
            self.forward_links[-end_num_1] = [-bridge_num]
            self.forward_links[-end_num_2] = [-bridge_num]

            # Finally, we need to check to see if there were any paths through the junction. If so,
            # they need to be adjusted to contain the new segment.
            for name, segs in self.paths.iteritems():
                self.paths[name] = insert_num_in_list(segs, start_num_1, end_num_1, bridge_num)
                self.paths[name] = insert_num_in_list(segs, start_num_1, end_num_2, bridge_num)
                self.paths[name] = insert_num_in_list(segs, start_num_2, end_num_1, bridge_num)
                self.paths[name] = insert_num_in_list(segs, start_num_2, end_num_2, bridge_num)
                self.paths[name] = insert_num_in_list(segs, -end_num_1, -start_num_1, -bridge_num)
                self.paths[name] = insert_num_in_list(segs, -end_num_1, -start_num_2, -bridge_num)
                self.paths[name] = insert_num_in_list(segs, -end_num_2, -start_num_1, -bridge_num)
                self.paths[name] = insert_num_in_list(segs, -end_num_2, -start_num_2, -bridge_num)

    def get_next_available_seg_number(self):
        '''
        This function finds the largest used segment number and returns the next 
        '''
        current_largest = max(self.segments.iterkeys())
        return current_largest + 1

    def get_depth_string(self, segment):
        '''
        Given a particular segment, this function returns a string with the segment's copy depths
        (if it has any).
        '''
        if segment.number not in self.copy_depths:
            return ''
        return ', '.join(['%.3f' % x for x in self.copy_depths[segment.number]])

    def get_copy_number_colour(self, segment):
        '''
        Given a particular segment, this function returns a colour string based on the copy number.
        '''
        if segment.number not in self.copy_depths:
            return 'black'
        copy_number = len(self.copy_depths[segment.number])
        if copy_number == 1:
            return 'forestgreen'
        if copy_number == 2:
            return 'gold'
        if copy_number == 3:
            return 'darkorange'
        else: # 4+
            return 'red'

    # def determine_copy_depth(self):

    #     one_link_segments = [x for x in self.segments.values() if self.at_most_one_link_per_end(x)]

    #     for seg in one_link_segments:
    #         depth = seg.depth

    #     base_depths = []
    #     for seg in one_link_segments:
    #         base_depths += [seg.depth] * seg.get_length()
    #     base_depths = sorted(base_depths)
    #     median_depth = get_median(base_depths)
    #     absolute_deviations = [abs(x - median_depth) for x in base_depths]
    #     median_absolute_deviation = 1.4826 * get_median(sorted(absolute_deviations))

    #     print('median_depth', median_depth)
    #     print('median_absolute_deviation', median_absolute_deviation)

    #     for seg in one_link_segments:
    #         depth = seg.depth
    #         robust_z_score = (seg.depth - median_depth) / median_absolute_deviation
    #         print(seg.number, seg.depth, robust_z_score)


    #     # depth_file = open('/Users/Ryan/Desktop/depths.txt', 'w')
    #     # for depth in base_depths:
    #     #     depth_file.write(str(depth) + '\n')
    #     # depth_file.close()







    def determine_copy_depth(self, minimum_auto_single):
        '''
        This function iteratively applies the various methods for assigning copy depth to segments
        until no more assignments can be made.  It may not succeed in assigning copy depths to all
        segments, as some segments will have strange/difficult connections or depth which prevent
        automatic copy depth determination.
        '''
        while True:
            assignments = self.assign_single_copy_depth(minimum_auto_single)
            self.determine_copy_depth_part_2()
            if not assignments:
                break

    def determine_copy_depth_part_2(self):
        while self.merge_copy_depths():
            pass
        if self.redistribute_copy_depths():
            self.determine_copy_depth_part_2()
        while self.simple_loop_copy_depths():
            pass

    def assign_single_copy_depth(self, minimum_auto_single):
        '''
        This function assigns a single copy to the longest segment with no more than one link per
        end.
        '''
        segments = sorted(self.get_segments_without_copies(), key=lambda x: x.get_length(), reverse=True)
        for segment in segments:
            if segment.get_length() >= minimum_auto_single and \
               self.at_most_one_link_per_end(segment):
                self.copy_depths[segment.number] = [segment.depth]
                print('assign_single_copy_segments:', segment.number) # TEMP
                return 1
        print('assign_single_copy_segments: ') # TEMP
        return 0

    def merge_copy_depths(self):
        '''
        This function looks for segments where they have input on one end where:
          1) All input segments have copy depth assigned.
          2) All input segments exclusively input to this segment.
        All such cases are evaluated, and the segment with the lowest error (if that error is below
        the allowed error margin) is assigned copy depths, scaling the inputs so their sum
        exactly matches the segment's depth.
        '''
        segments = self.get_segments_without_copies()
        if not segments:
            print('merge_copy_depths:           ') # TEMP
            return 0

        error_margin = 1.0 # TEMP: EITHER REMOVE LATER OR MAKE A PARAMETER
        
        best_segment_num = None
        best_new_depths = []
        lowest_error = float('inf')

        for segment in segments:
            num = segment.number
            exclusive_inputs = self.get_exclusive_inputs(num)
            exclusive_outputs = self.get_exclusive_outputs(num)
            in_depth_possible = exclusive_inputs and self.all_have_copy_depths(exclusive_inputs)
            out_depth_possible = exclusive_outputs and self.all_have_copy_depths(exclusive_outputs)
            if in_depth_possible:
                depths, error = self.scale_copy_depths_from_source_segments(num, exclusive_inputs)
                if error < lowest_error:
                    lowest_error = error
                    best_segment_num = num
                    best_new_depths = depths
            if out_depth_possible:
                depths, error = self.scale_copy_depths_from_source_segments(num, exclusive_outputs)
                if error < lowest_error:
                    lowest_error = error
                    best_segment_num = num
                    best_new_depths = depths
        if best_segment_num and lowest_error < error_margin:
            print('merge_copy_depths:          ', best_segment_num) # TEMP
            self.copy_depths[best_segment_num] = best_new_depths
            return 1
        else:
            print('merge_copy_depths:           ') # TEMP
            return 0

    def redistribute_copy_depths(self):
        '''
        This function deals with the easier case of copy depth redistribution: where one segments
        with copy depth leads exclusively to multiple segments without copy depth.
        We will then try to redistribute the source segment's copy depths among the destination
        segments.  If it can be done within the allowed error margin, the destination segments will
        get their copy depths.
        '''
        error_margin = 1.0 # TEMP: EITHER REMOVE LATER OR MAKE A PARAMETER

        segments = self.get_segments_with_two_or_more_copies()
        if not segments:
            print('redistribute_copy_depths:    ') # TEMP
            return 0
        assignment_count = 0
        for segment in segments:
            num = segment.number
            connections = self.get_exclusive_inputs(num)
            if not connections or self.all_have_copy_depths(connections):
                connections = self.get_exclusive_outputs(num)
            if not connections or self.all_have_copy_depths(connections):
                continue

            # If we got here, then we can try to redistribute the segment's copy depths to its
            # connections which are lacking copy depth.
            copy_depths = self.copy_depths[num]
            bins = [[]] * len(connections)
            targets = [None if x not in self.copy_depths else len(self.copy_depths[x]) for x in connections]
            arrangments = shuffle_into_bins(copy_depths, bins, targets)
            if not arrangments:
                continue

            lowest_error = float('inf')
            for arrangment in arrangments:
                error = self.get_error_for_multiple_segments_and_depths(connections, arrangment)
                if error < lowest_error:
                    lowest_error = error
                    best_arrangement = arrangment
            if lowest_error < error_margin:
                if self.assign_copy_depths_where_needed(connections, best_arrangement):
                    print('redistribute_copy_depths:   ', num) # TEMP
                    return 1

        print('redistribute_copy_depths:    ') # TEMP
        return 0

    def simple_loop_copy_depths(self):
        '''
        This function assigns copy depths to simple loop structures.  It will only assign copy
        depths in cases where the loop occurs once - higher repetition loops will not be given copy
        depths due to the increasing uncertainty in repetition counts.
        '''
        assignment_count = 0
        # TO DO
        # TO DO
        # TO DO
        # TO DO
        # TO DO
        # TO DO
        print('simple_loop_copy_depths:     ') # TEMP
        return assignment_count
        
    def at_most_one_link_per_end(self, segment):
        '''
        Returns True if the given segment has no more than one link on either end.
        '''
        num = segment.number
        if num in self.forward_links and len(self.forward_links[num]) > 1:
            return False
        if num in self.reverse_links and len(self.reverse_links[num]) > 1:
            return False
        return True

    def all_have_copy_depths(self, segment_numbers):
        '''
        Takes a list of segment numbers and returns whether every segment in the list has copy
        depths assigned.
        '''
        for num in segment_numbers:
            if num not in self.copy_depths:
                return False
        return True

    def scale_copy_depths_from_source_segments(self, segment_number, source_segment_numbers):
        '''
        Using a list of segments which are the source of copy depth, this function scales them so
        that their sum matches the depth of the given segment.
        It returns:
          1) a list of depth numbers
          2) the error (i.e. the degree of scaling which had to occur)
        It assumes that all of the source segments definitely have copy depths.
        '''
        source_depths = []
        for num in source_segment_numbers:
            source_depths += self.copy_depths[num]
        target_depth = self.segments[segment_number].depth
        return self.scale_copy_depths(target_depth, source_depths)

    def scale_copy_depths(self, target_depth, source_depths):
        '''
        This function takes the source depths and scales them so their sum matches the target
        depth.  It returns the scaled depths and the error.
        '''
        source_depth_sum = sum(source_depths)
        scaling_factor = target_depth / source_depth_sum
        scaled_depths = sorted([scaling_factor * x for x in source_depths], reverse=True)
        error = get_error(source_depth_sum, target_depth)
        return scaled_depths, error

    def get_segments_without_copies(self):
        '''
        Returns a list of the graph segments lacking copy depth information.
        '''
        return [x for x in self.segments.values() if x.number not in self.copy_depths]

    def get_segments_with_two_or_more_copies(self):
        return [x for x in self.segments.values() if x.number in self.copy_depths and len(self.copy_depths[x.number]) > 1]

    def get_error_for_multiple_segments_and_depths(self, segment_numbers, copy_depths):
        '''
        For the given segments, this function assesses how well the given copy depths match up.
        The maximum error for any segment is what's returned at the end.
        '''
        max_error = 0
        for i, num in enumerate(segment_numbers):
            segment_depth = self.segments[num].depth
            depth_sum = sum(copy_depths[i])
            max_error = max(max_error, get_error(depth_sum, segment_depth))
        return max_error

    def assign_copy_depths_where_needed(self, segment_numbers, new_depths):
        '''
        For the given segments, this function assigns the corresponding copy depths, scaled to fit
        the segment.  If a segment already has copy depths, it is skipped (i.e. this function only
        write new copy depths, doesn't overwrite existing ones).
        It will only create copy depths if doing so is within the allowed error margin.
        '''
        error_margin = 1.0 # TEMP: EITHER REMOVE LATER OR MAKE A PARAMETER

        success = False
        for i, num in enumerate(segment_numbers):
            if num not in self.copy_depths:
                new_copy_depths, error = self.scale_copy_depths(self.segments[num].depth, new_depths[i])
                if error <= error_margin:
                    self.copy_depths[num] = new_copy_depths
                    success = True
        return success




class Segment(object):
    '''
    This hold a graph segment with a number, depth, direction and sequence.
    '''
    def __init__(self, number, depth, sequence, positive):
        self.number = number
        self.depth = depth
        self.forward_sequence = ''
        self.reverse_sequence = ''
        if positive:
            self.forward_sequence = sequence
        else:
            self.reverse_sequence = sequence

    def __repr__(self):
        if len(self.forward_sequence) > 6:
            seq_string = self.forward_sequence[:3] + '...' + self.forward_sequence[-3:]
        else:
            seq_string = self.forward_sequence
        return str(self.number) + ' (' + seq_string + ')'

    def add_sequence(self, sequence, positive):
        if positive:
            self.forward_sequence = sequence
        else:
            self.reverse_sequence = sequence

    def build_other_sequence_if_necessary(self):
        if not self.forward_sequence:
            self.forward_sequence = reverse_complement(self.reverse_sequence)
        if not self.reverse_sequence:
            self.reverse_sequence = reverse_complement(self.forward_sequence)

    def divide_depth(self, divisor):
        self.depth /= divisor

    def get_fastg_header(self, positive):
        '''
        Returns a SPAdes-style FASTG header, without the leading '>' or ending ';'.
        '''
        header = 'EDGE_' + str(self.number) + '_length_' + str(len(self.forward_sequence)) + '_cov_' + str(self.depth)
        if not positive:
            header += "'"
        return header

    def get_length(self):
        return len(self.forward_sequence)

    def get_length_no_overlap(self, overlap):
        return len(self.forward_sequence) - overlap

    def is_homopolymer(self):
        '''
        Returns True if the segment's sequence is made up of only one base.
        '''
        if len(self.forward_sequence) == 0:
            return False
        first_base = self.forward_sequence[0].lower()
        for base in self.forward_sequence[1:]:
            if base.lower() != first_base:
                return False
        return True

    def gfa_segment_line(self):
        '''
        Returns an entire S line for GFA output, including the newline.
        '''
        s_line = 'S\t'
        s_line += str(self.number) + '\t'
        s_line += self.forward_sequence + '\t'
        s_line += 'LN:i:' + str(self.get_length()) + '\t'
        s_line += 'DP:f:' + str(self.depth) + '\n'
        return s_line

    def save_to_fasta(self, fasta_filename):
        '''
        Saves the segment's sequence to FASTA file.
        '''
        fasta = open(fasta_filename, 'w')
        fasta.write('>' + self.get_fastg_header(True) + '\n')
        fasta.write(add_line_breaks_to_sequence(self.forward_sequence, 60))
        fasta.close()



def get_error(source, target):
    '''
    Returns the relative error from trying to assign the source value to the target value.
    E.g. if source = 1.6 and target = 2.0, the error is 0.2
    '''
    if target > 0.0:
        return abs(source - target) / target
    else:
        return float('inf')

def within_error_margin(val_1, val_2, error_margin):
    '''
    Returns whether val_1 is within the error margin of val_2.
    I.e. val_2 * (1 - em) <= val_1 <= val_2 * (1 + em)
    E.g. if val_2 is 100 and the error margin is 0.3, then val_1 must be in the range of 70 to 130
         (inclusive) for this function to return true.
    '''
    return val_1 >= val_2 * (1 - error_margin) and val_1 <= val_2 * (1 + error_margin)

def shuffle_into_bins(items, bins, targets):
    '''
    Shuffle items into bins in all possible arrangements that satisfy these conditions:
      1) All bins must have at least one item.
      2) Any bins with a specified target must have exactly that number of items.
    '''
    arrangements = []

    # If there are items not yet in a bin, place the first item in each possible bin and call this
    # function recursively.
    if items:
        for i, _ in enumerate(bins):
            bins_copy = [list(x) for x in bins]
            bins_copy[i].append(items[0])
            arrangements += shuffle_into_bins(items[1:], bins_copy, targets)

    # If all items are in a bin, all bins have at least one item and any bins with a target have
    # the appropriate amount, then add the arrangement to the results.
    elif all(x for x in bins) and \
         all([not target or target == len(bins[i]) for i, target in enumerate(targets)]):
            arrangements.append(bins)

    return arrangements

def get_median(sorted_list):
    count = len(sorted_list)
    index = (count - 1) // 2
    if (count % 2):
        return sorted_list[index]
    else:
        return (sorted_list[index] + sorted_list[index + 1]) / 2.0

def get_headers_and_sequences(filename):
    '''
    Reads through a SPAdes assembly graph file and returns two lists:
    1) the headers for each segment (without the leading '>')
    2) the sequences for each segment
    '''
    headers = []
    sequences = []
    header = ''
    sequence = ''
    graph_file = open(filename, 'r')
    for line in graph_file:
        line = line.strip()
        if not line:
            continue
        if line[0] == '>':
            if header:
                headers.append(header)
                sequences.append(sequence)
                header = ''
                sequence = ''
            header = line[1:]
        else:
            sequence += line
    if header:
        headers.append(header)
        sequences.append(sequence)
    return headers, sequences

def reverse_complement(seq):
    '''
    Given a DNA sequences, this function returns the reverse complement sequence.
    '''
    rev_comp = ''
    for i in reversed(range(len(seq))):
        rev_comp += complement_base(seq[i])
    return rev_comp

def complement_base(base):
    '''
    Given a DNA base, this returns the complement.
    '''
    forward = 'ATGCatgcRYSWKMryswkmBDHVbdhvNn.-?'
    reverse = 'TACGtacgYRSWMKyrswmkVHDBvhdbNn.-?N'
    return reverse[forward.find(base)]

def get_unsigned_number_from_header(header):
    '''
    Input: a SPAdes FASTG header line
    Output: an int for the segment number (always positive)
    '''
    return int(header.split('_')[1])

def get_signed_number_from_header(header):
    '''
    Input: a SPAdes FASTG header line
    Output: an int for the segment number (always positive)
    '''
    number = get_unsigned_number_from_header(header)
    if not is_header_positive(header):
        number *= -1
    return number

def is_header_positive(header):
    '''
    Input: a SPAdes FASTG header line
    Output: True if the header is for a positive segment, False for a negative segment.
    '''
    if header[-1] == ';':
        header = header[:-1]
    return header.split(':')[0][-1] != "'"

def get_depth_from_header(header):
    '''
    Input: a SPAdes FASTG header line
    Output: The segment's depth
    '''
    header = header.split(':')[0]
    if header[-1] == "'":
        header = header[:-1]
    parts = header.split('_')
    depth_str = parts[5]
    if depth_str.endswith(';'):
        depth_str = depth_str[:-1]
    if depth_str.endswith("'"):
        depth_str = depth_str[:-1]
    return float(depth_str)

def get_links_from_header(header):
    '''
    Input: a SPAdes FASTG header line
    Output: a tuple of starting segment and a list of ending segments
    '''
    if header[-1] == ';':
        header = header[:-1]
    start = get_signed_number_from_header(header)
    end_list = []
    pieces = header.split(':')
    if len(pieces) > 1:
        ends = pieces[1].split(',')
        for end in ends:
            end_list.append(get_signed_number_from_header(end))
    return (start, end_list)

def build_rc_links_if_necessary(links):
    '''
    This function makes sure that every link also has a reverse complement.  E.g. if there is a
    link from 5+ to 7-, there should also be a link from 7+ to 5-.
    '''
    new_links = links.copy()
    for start, ends in links.iteritems():
        rc_start = -start
        for end in ends:
            rc_end = -end
            if rc_end not in new_links:
                new_links[rc_end] = []
            if rc_start not in new_links[rc_end]:
                new_links[rc_end].append(rc_start)
    return new_links

def build_reverse_links(links):
    '''
    This function builds a dictionary of links going the other way.  I.e. if given a dictionary
    of start to end links, it will return a dictionary of end to start links.
    '''
    reverse_links = {}
    for start, ends in links.iteritems():
        for end in ends:
            if end not in reverse_links:
                reverse_links[end] = []
            reverse_links[end].append(start)
    return reverse_links

def add_line_breaks_to_sequence(sequence, length):
    '''
    Wraps sequences to the defined length.  All resulting sequences end in a line break.
    '''
    seq_with_breaks = ''
    while len(sequence) > length:
        seq_with_breaks += sequence[:length] + '\n'
        sequence = sequence[length:]
    if len(sequence) > 0:
        seq_with_breaks += sequence
        seq_with_breaks += '\n'
    return seq_with_breaks

def remove_nums_from_links(links, nums_to_remove):
    '''
    This function rebuilds a link dictionary excluding the given numbers.
    nums_to_remove is expected to be a list of positive (unsigned) segment numbers.
    '''
    new_links = {}
    for n_1, n_2 in links.iteritems():
        if abs(n_1) not in nums_to_remove:
            new_links[n_1] = [x for x in n_2 if abs(x) not in nums_to_remove]
            if new_links[n_1] == []:
                del new_links[n_1]
    return new_links

def all_segments_are_one_base(segments):
    '''
    This function returns true if all given segments have nothing but one base.
    '''
    non_empty_segments = [x for x in segments if x.get_length() > 0]
    if not non_empty_segments:
        return False
    base = non_empty_segments[0].forward_sequence[0].lower()
    for segment in non_empty_segments:
        if not segment.is_homopolymer():
            return False
        forward_base = segment.forward_sequence[0].lower()
        reverse_base = segment.reverse_sequence[0].lower()
        if forward_base != base and reverse_base != base:
            return False
    return True

def is_link_positive(start, end):
    '''
    Returns True if the link is 'positive'.  This is a somewhat arbitrary call that allows us to
    only get one link per RC pair.
    A link is positive if:
      1) Both segments are positive
      2) It has no RC link (i.e. is its own RC)
      3) The starting segment has a higher absolute value than the ending segment.
    '''
    if start > 0 and end > 0:
        return True
    if start < 0 and end < 0:
        return False
    if start == -end:
        return True
    return abs(start) > abs(end)

def get_sign_string(num):
    '''
    Returns '+' for positive numbers (and zero) and '-' for negative numbers.
    '''
    if num >= 0:
        return '+'
    else:
        return '-'

def int_to_signed_string(num):
    '''
    Takes an integer and returns a string with the sign at the end.
    Examples:
      5 -> 5+
      -6 -> 6-
    '''
    return str(abs(num)) + get_sign_string(num)

def signed_string_to_int(signed_str):
    '''
    Takes a string with the sign at the end and returns an integer.
    '''
    sign = signed_str[-1]
    num = int(signed_str[:-1])
    if sign == '+':
        return num
    else:
        return -num

def insert_num_in_list(lst, val_1, val_2, insert_val):
    '''
    If the list lst contains val_1 immediately followed by val_2, the function returns a new list
    with insert_val between them. If the list does not contain that sequence of values, this
    function just returns the original list.
    '''
    if len(lst) < 2:
        return lst
    new_list = []
    for i, val in enumerate(lst[:-1]):
        next_val = lst[i+1]
        new_list.append(val)
        if val == val_1 and next_val == val_2:
            new_list.append(insert_val)
    new_list.append(lst[-1])
    return new_list 

def find_replace_in_list(lst, pattern, replacement):
    '''
    This function looks for the given pattern in the list and if found, replaces it.
    Example: find_replace_in_list([1,5,8,3], [5,8], 7) -> [1,7,3]
    If there are multiple occurrences, it will replace them all.
    '''
    replacement_made = True
    while replacement_made:
        replacement_made = False
        for i, _ in enumerate(lst):
            if lst[i] == pattern[0] and lst[i:i+len(pattern)] == pattern:
                replacement_made = True
                lst = lst[:i] + [replacement] + lst[i+len(pattern):]
                break
    return lst

def split_path(path, seg):
    '''
    If val is in the list, it returns multiple lists split at that point, excluding val.
    Sort of like the string split function, but it throws out lists of 1 (because they aren't
    useful as paths).
    '''
    return_paths = []
    while seg in path:
        seg_i = path.index(seg)
        return_paths.append(path[:seg_i])
        path = path[seg_i+1:]
    return_paths.append(path)
    return_paths = [x for x in return_paths if len(x) > 1]
    return return_paths

def split_path_multiple(path, segs):
    '''
    Like split_path, but vals is a list of vals, all of which split the list.
    '''
    path_parts = [path]
    for seg in segs:
        new_path_parts = []
        for part in path_parts:
            new_path_parts += split_path(part, seg)
        path_parts = new_path_parts
    return path_parts


