# cython: boundscheck=False, wraparound=False, freethreading_compatible=True

cpdef inline int fuzzy_match_score_single(str query, str candidate):
    """
    Calculate score for fuzzy matching of single query word.
    Consecutive matches will have larger score.
    Matches closer to the start of the candidate string will have larger score.
    Score is not limited.
    """
    cdef int qlen = len(query)
    cdef int clen = len(candidate)
    cdef int qpos = 0, cpos = 0
    cdef int score = 0
    cdef int last_match_pos = -1
    cdef str query_lower = query.lower()
    cdef str candidate_lower = candidate.lower()

    while qpos < qlen and cpos < clen:
        if query_lower[qpos] == candidate_lower[cpos]:
            if cpos == last_match_pos + 1:
                score += 10   # consecutive match adds more score
            else:
                score += 1   # match after some gap
            last_match_pos = cpos
            qpos += 1
        cpos += 1

    if qpos == qlen:
        # bonus for match starting early in candidate
        score += max(0, 10 - last_match_pos)
        return score
    return 0


cpdef int fuzzy_match_score(str query, str candidate):
    """
    Calculate score for fuzzy matching of query containing one or multiple words.
    Consecutive matches will have larger score.
    Matches closer to the start of the candidate string will have larger score.
    Score is not limited.
    """
    cdef str word
    cdef int score

    cdef int total_score = 0
    cdef list words = query.split()

    for word in words:
        score = fuzzy_match_score_single(word, candidate)
        if score == 0:
            return 0
        total_score += score
    return total_score
